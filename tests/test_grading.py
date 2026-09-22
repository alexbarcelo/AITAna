import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from aitana.grading import grading
from aitana.grading.grading import build_system_prompt, grade_answer
from aitana.grading.models import DEFAULT_GRADING_SCALE, Grade, GradeTrace, Question, grade_schema_for_scale


class _FakeChatModel:
    """Stands in for the `chat_model` argument. `grade_answer` always routes
    through `create_agent` now (see grading._grade_with_tools), so this only
    needs to be a distinguishable object to pass through as `model` -- tests
    that reach the LLM call fake out `create_agent` itself rather than
    relying on any real chat-model interface here."""

    def __init__(self, grade: Grade):
        self._grade = grade


def _fake_create_agent(expected: Grade, captured: dict | None = None):
    """Builds a fake `create_agent` replacement whose compiled "agent" just
    returns `expected` as the structured_response, capturing the kwargs
    create_agent was called with (and the invoke() state) into `captured`
    for tests that want to assert on them."""
    if captured is None:
        captured = {}

    class _FakeCompiledAgent:
        def invoke(self, state):
            captured["state"] = state
            return {"structured_response": expected}

    def _create_agent(*, model, tools, response_format):
        captured["model"] = model
        captured["tools"] = tools
        captured["response_format"] = response_format
        return _FakeCompiledAgent()

    return _create_agent


def _question(**overrides) -> Question:
    defaults = dict(
        id="answer1",
        title="Some question",
        question="What happened and why?",
        rubric="Rubric text",
        expected_points=["a key point"],
    )
    defaults.update(overrides)
    return Question(**defaults)


def test_grade_answer_short_circuits_blank_answers_without_calling_llm():
    question = _question()
    fake_model = _FakeChatModel(Grade(level="solid", feedback="should never be returned"))

    result = grade_answer(fake_model, question, "   ", DEFAULT_GRADING_SCALE)

    assert result.level == "not_attempted"  # the default scale's first (worst) key
    assert "No answer" in result.feedback


def test_grade_answer_blank_short_circuit_uses_first_key_of_a_custom_scale():
    """The blank-answer short-circuit doesn't hardcode "not_attempted" --
    it's whatever a rubric's grading_scale lists first, since a custom scale
    might not have a level by that name at all (e.g. a pass/fail scale)."""
    question = _question()
    fake_model = _FakeChatModel(Grade(level="pass", feedback="should never be returned"))
    custom_scale = {"fail": "Did not meet the bar.", "pass": "Met the bar."}

    result = grade_answer(fake_model, question, "", custom_scale)

    assert result.level == "fail"


def test_grade_answer_returns_llm_structured_output_for_nonblank_answers(monkeypatch):
    question = _question()
    expected = Grade(level="almost_there", feedback="Close, but missing the fix.")
    fake_model = _FakeChatModel(expected)

    monkeypatch.setattr(grading, "create_agent", _fake_create_agent(expected))

    result = grade_answer(fake_model, question, "My answer text", DEFAULT_GRADING_SCALE)

    assert result == expected


def test_build_system_prompt_includes_question_rubric_and_expected_points():
    question = _question(
        question="Why can't the containers resolve each other?",
        rubric="Explain the isolation issue.",
        expected_points=["mentions DNS", "mentions fix"],
    )

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "Why can't the containers resolve each other?" in prompt
    assert "Explain the isolation issue." in prompt
    assert "- mentions DNS" in prompt
    assert "- mentions fix" in prompt


def test_build_system_prompt_handles_no_expected_points():
    question = _question(expected_points=[])

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "(none specified)" in prompt


def test_build_system_prompt_omits_context_section_when_absent():
    question = _question(context=None)

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "# Context" not in prompt


def test_build_system_prompt_includes_context_when_present():
    question = _question(context="The student had already run `docker compose up -d`.")

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "# Context" in prompt
    assert "The student had already run `docker compose up -d`." in prompt


def test_build_system_prompt_renders_grading_scale_as_grading_section():
    question = _question()
    custom_scale = {"fail": "Did not meet the bar.", "pass": "Met the bar."}

    prompt = build_system_prompt(question, custom_scale)

    assert "# Grading" in prompt
    assert "- fail: Did not meet the bar." in prompt
    assert "- pass: Met the bar." in prompt
    # The old hardcoded 4-level block shouldn't leak in for a scale that
    # doesn't define those levels.
    assert "not_attempted" not in prompt


def test_grade_schema_for_scale_accepts_only_configured_levels():
    schema = grade_schema_for_scale({"fail": "...", "pass": "..."})

    assert schema(level="pass", feedback="ok").level == "pass"
    with pytest.raises(ValidationError):
        schema(level="almost_there", feedback="not a level in this scale")


def test_build_system_prompt_omits_python_tool_section_by_default():
    question = _question()

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "# Tool available" not in prompt


def test_build_system_prompt_includes_python_tool_section_when_needs_python_sandbox():
    question = _question(needs_python_sandbox=True)

    prompt = build_system_prompt(question, DEFAULT_GRADING_SCALE)

    assert "# Tool available" in prompt
    assert "Python sandbox" in prompt


def test_grade_answer_passes_no_tools_to_create_agent_when_sandbox_not_needed(monkeypatch):
    """A question that doesn't need the sandbox still goes through
    create_agent (see grading._grade_with_tools) but with an empty tools
    list, rather than skipping the agent path entirely."""

    question = _question(needs_python_sandbox=False)
    expected = Grade(level="solid", feedback="Looks right.")
    fake_model = _FakeChatModel(expected)
    captured: dict = {}

    monkeypatch.setattr(grading, "create_agent", _fake_create_agent(expected, captured))

    result = grade_answer(fake_model, question, "My answer text", DEFAULT_GRADING_SCALE)

    assert result == expected
    assert captured["tools"] == []


def test_grade_answer_emits_trace_on_blank_short_circuit():
    """on_trace still fires for a blank answer, even though no LLM call is
    made -- system_prompt is populated (it's cheap to build), but there's no
    thinking/tool_calls/usage to report."""
    question = _question()
    fake_model = _FakeChatModel(Grade(level="solid", feedback="unused"))
    captured: list[GradeTrace] = []

    result = grade_answer(
        fake_model, question, "   ", DEFAULT_GRADING_SCALE, provider="openai", model="gpt-4o-mini", on_trace=captured.append
    )

    assert result.level == "not_attempted"
    assert len(captured) == 1
    trace = captured[0]
    assert trace.blank_short_circuit is True
    assert trace.provider == "openai"
    assert trace.model == "gpt-4o-mini"
    assert "What happened and why?" in trace.system_prompt
    assert trace.student_answer == "   "
    assert trace.grade == result
    assert trace.thinking is None
    assert trace.tool_calls == []
    assert trace.prompt_tokens is None


def test_grade_answer_emits_trace_for_a_real_llm_call(monkeypatch):
    question = _question()
    expected = Grade(level="almost_there", feedback="Close.")
    fake_model = _FakeChatModel(expected)
    captured: list[GradeTrace] = []

    monkeypatch.setattr(grading, "create_agent", _fake_create_agent(expected))

    result = grade_answer(
        fake_model,
        question,
        "My answer text",
        DEFAULT_GRADING_SCALE,
        provider="openai",
        model="gpt-4o-mini",
        on_trace=captured.append,
    )

    assert result == expected
    assert len(captured) == 1
    trace = captured[0]
    assert trace.blank_short_circuit is False
    assert trace.grade == expected
    assert trace.student_answer == "My answer text"
    assert trace.elapsed_seconds >= 0


def test_grade_answer_without_on_trace_calls_nothing():
    """Every pre-existing call site (no on_trace given) sees no behavior
    change at all -- the callback is simply never invoked."""
    question = _question()
    fake_model = _FakeChatModel(Grade(level="solid", feedback="f"))

    result = grade_answer(fake_model, question, "", DEFAULT_GRADING_SCALE)

    assert result.level == "not_attempted"


def test_grade_answer_trace_extracts_tool_calls_and_thinking(monkeypatch):
    """A needs_python_sandbox question's agent run produces intermediate
    AIMessage/ToolMessage traffic -- the trace should capture the tool call
    (name/args/output) and any reasoning content/usage metadata found on the
    AIMessages, not just the final structured grade."""
    from langchain_core.messages import ToolMessage

    question = _question(needs_python_sandbox=True)
    expected = Grade(level="solid", feedback="Verified via code.")
    fake_model = _FakeChatModel(Grade(level="solid", feedback="unused"))
    captured: list[GradeTrace] = []

    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "python_sandbox", "args": {"code": "print(1+1)"}, "id": "call_1"}],
            additional_kwargs={"reasoning_content": "Let me check by running the code."},
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        ),
        ToolMessage(content="2", tool_call_id="call_1"),
        AIMessage(content="Final.", usage_metadata={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}),
    ]

    def _create_agent(*, model, tools, response_format):
        class _FakeCompiledAgent:
            def invoke(self, state):
                return {"structured_response": expected, "messages": messages}

        return _FakeCompiledAgent()

    monkeypatch.setattr(grading, "create_agent", _create_agent)

    result = grade_answer(
        fake_model, question, "My answer text", DEFAULT_GRADING_SCALE, provider="openai", model="gpt-4o-mini", on_trace=captured.append
    )

    assert result == expected
    trace = captured[0]
    assert trace.thinking == "Let me check by running the code."
    assert trace.tool_calls == [{"tool": "python_sandbox", "args": {"code": "print(1+1)"}, "output": "2"}]
    assert trace.prompt_tokens == 30
    assert trace.completion_tokens == 13
    assert trace.total_tokens == 43


def test_extract_thinking_returns_none_with_no_recognizable_content():
    from aitana.grading.grading import _extract_thinking

    assert _extract_thinking([AIMessage(content="just a plain answer")]) is None
    assert _extract_thinking([]) is None


def test_extract_thinking_reads_reasoning_content_key():
    """Ollama and OpenRouter's shape (see llm.py's get_chat_model docstring)
    -- a plain string under additional_kwargs['reasoning_content']."""
    from aitana.grading.grading import _extract_thinking

    messages = [AIMessage(content="answer", additional_kwargs={"reasoning_content": "because X implies Y"})]
    assert _extract_thinking(messages) == "because X implies Y"


def test_extract_thinking_reads_openai_responses_api_reasoning_block():
    """OpenAI's Responses-API shape: a {"type": "reasoning", "summary": [...]}
    content block, with the actual text nested under `summary`, not directly
    on the block -- a real bug this project's first extraction pass had
    (checked `block["text"]` directly), caught by reading langchain-openai's
    own documented example rather than guessing."""
    from aitana.grading.grading import _extract_thinking

    messages = [
        AIMessage(
            content=[
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Step 1..."}, {"type": "summary_text", "text": "Step 2..."}]},
                {"type": "text", "text": "final answer"},
            ]
        )
    ]
    assert _extract_thinking(messages) == "Step 1...\n\nStep 2..."


def test_extract_thinking_reads_anthropic_thinking_block():
    """Anthropic's shape: a {"type": "thinking", "thinking": "..."} content
    block -- note the text key is `thinking`, not `text`."""
    from aitana.grading.grading import _extract_thinking

    messages = [
        AIMessage(content=[{"type": "thinking", "thinking": "Let me work through this..."}, {"type": "text", "text": "answer"}])
    ]
    assert _extract_thinking(messages) == "Let me work through this..."


def test_extract_thinking_surfaces_redacted_thinking_as_a_marker():
    """A redacted_thinking block has no recoverable text -- surfaced as an
    explicit marker so a trace reader can tell "reasoning happened but is
    hidden" apart from "no reasoning happened at all"."""
    from aitana.grading.grading import _extract_thinking

    messages = [AIMessage(content=[{"type": "redacted_thinking"}, {"type": "text", "text": "answer"}])]
    assert _extract_thinking(messages) == "[reasoning segment redacted by the provider]"


def test_grade_answer_routes_through_create_agent_when_needs_python_sandbox(monkeypatch):
    """The needs_python_sandbox path builds an agent (with the sandbox tool
    bound in) instead of calling with_structured_output directly, and reads
    the result back off the agent's `structured_response` state key.

    Uses a fake create_agent rather than a real Deno/Pyodide sandbox -- see
    grading/sandbox.py's `get_python_sandbox_tool` for why constructing the
    tool itself never requires Deno to be installed, which is what makes
    this fake safe to use without Deno present in the test environment.
    """
    expected = Grade(level="almost_there", feedback="Ran the code, close but off by one.")
    captured: dict = {}

    monkeypatch.setattr(grading, "create_agent", _fake_create_agent(expected, captured))
    question = _question(needs_python_sandbox=True)
    fake_model = _FakeChatModel(Grade(level="solid", feedback="should never be returned"))

    result = grade_answer(fake_model, question, "My answer text", DEFAULT_GRADING_SCALE)

    assert result == expected
    assert captured["model"] is fake_model
    assert len(captured["tools"]) == 1
    assert captured["tools"][0].name == "python_sandbox"
    messages = captured["state"]["messages"]
    assert messages[1].content == "My answer text"
