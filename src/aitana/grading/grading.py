"""Builds the grading prompt and invokes the LLM for a single question.

Prompt split:
  - SYSTEM message: grading instructions (rubric, expected points, grading
    scale) -- comes from the YAML config / rubric document, same for every
    student.
  - HUMAN message: the student's own answer text, verbatim.
"""

from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING, Callable

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .models import Grade, GradeTrace, Question, grade_schema_for_scale
from .sandbox import get_python_sandbox_tool

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are a teaching assistant grading a master's-level student's answer to a
hands-on lab question. Grade at a COARSE level only: the goal is fast,
useful feedback for the student and teacher, not a precise score.

# Question
{question}
{context_section}
# Grading instructions
{rubric}
{model_answer_section}
# Points a good answer should cover
{expected_points}
{python_tool_section}
# Grading
Assign exactly one level:
{grading_levels}

Then give short (2-3 sentence), specific feedback addressed directly to the
student: what they got right and, more importantly, what they are missing.
"""

# Only interpolated when Question.needs_python_sandbox is set (see
# build_system_prompt) -- the tool's own description (grading/sandbox.py)
# already tells the model how to call it; this just tells it *when* it's
# worth bothering, since most rubric text alone wouldn't suggest running code.
_PYTHON_TOOL_SECTION = """
# Tool available
You have a Python sandbox tool. Use it if running code would help you judge
this answer -- e.g. to check a claimed command's output or verify a
computation -- but it's optional: skip it if the rubric can be judged from
the answer text alone.
"""


def build_grading_section(grading_scale: dict[str, str]) -> str:
    """Render a rubric's grading_scale as the prompt's level-id: description
    list -- the part of the prompt that used to be a hardcoded 4-line block
    in SYSTEM_PROMPT_TEMPLATE itself, before grading scales became
    per-rubric (see Rubric.grading_scale's docstring)."""
    return "\n".join(f"- {level}: {description}" for level, description in grading_scale.items())


def build_system_prompt(question: Question, grading_scale: dict[str, str]) -> str:
    points = "\n".join(f"- {p}" for p in question.expected_points) or "(none specified)"
    context_section = f"\n# Context\n{question.context.strip()}\n" if question.context else ""
    # Only rendered when the question has one (see Question.model_answer's
    # docstring) -- most questions don't, and omitting the heading entirely
    # avoids implying "no canonical answer" is itself meaningful feedback.
    model_answer_section = f"\n# Model answer\n{question.model_answer.strip()}\n" if question.model_answer else ""
    python_tool_section = _PYTHON_TOOL_SECTION if question.needs_python_sandbox else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        question=question.question.strip(),
        context_section=context_section,
        rubric=question.rubric.strip(),
        model_answer_section=model_answer_section,
        expected_points=points,
        python_tool_section=python_tool_section,
        grading_levels=build_grading_section(grading_scale),
    )


def _grade_with_tools(chat_model: BaseChatModel, tools: list[BaseTool], system_prompt: str, student_answer: str, schema: type) -> dict:
    """Call an agent with the available tools for this answer.
    Note that this is typically overkill for no-tool answers, but
    you know: DRY.

    Returns the agent's full invoke() result (not just structured_response)
    -- grade_answer's trace-building needs the intermediate `messages` too
    (for whatever thinking/tool-call content they carry), not just the final
    parsed grade.
    """
    agent = create_agent(
        model=chat_model,
        tools=tools,
        response_format=schema,
    )
    return agent.invoke({"messages": [SystemMessage(content=system_prompt), HumanMessage(content=student_answer)]})


def _model_identity(chat_model: BaseChatModel) -> str:
    """Best-effort model name for a GradeTrace when the caller doesn't pass
    one explicitly -- all four of grading/llm.py's providers expose a
    `model_name` or `model` attribute (`ChatOpenRouter` aliases its field as
    `model_name`; `ChatOpenAI`/`ChatAnthropic`/`ChatOllama` all use `model`
    directly), but neither is guaranteed across every `BaseChatModel`
    implementation in general, hence the class-name fallback. In practice
    this is only a fallback for a fallback: every real call site
    (`worker/tasks.py`) passes `provider`/`model` explicitly from `Settings`,
    so this only fires if a future caller doesn't."""
    return getattr(chat_model, "model_name", None) or getattr(chat_model, "model", None) or type(chat_model).__name__


def _extract_thinking(messages: list[BaseMessage]) -> str | None:
    """Best-effort extraction of any reasoning/"thinking" content a
    provider's response included on an AIMessage, normalized to one flat
    string regardless of which of this project's four providers produced it
    -- `GradeTrace.thinking` is a provider-agnostic field, not a grab-bag of
    each provider's own response shape (see llm.py's `get_chat_model` for
    what actually turns reasoning *on* per provider in the first place).

    Checked shapes, each verified against the actual installed client
    source, not guessed (see AGENTS.md's "Reasoning/thinking capture across
    providers"):

      - Ollama (`langchain-ollama` 1.1.0) and OpenRouter
        (`langchain-openrouter` 0.2.9) both normalize their provider's raw
        reasoning text into `additional_kwargs["reasoning_content"]` (a
        plain string) -- the same key, so one check covers both.
      - OpenAI (`langchain-openai` 1.6.0), Responses API only: a
        `{"type": "reasoning", "summary": [{"text": ...}, ...]}` block
        inside `message.content` (a list of blocks, not a plain string,
        once `output_version="responses/v1"` is set) -- the text lives
        nested under `summary`, not directly on the block.
      - Anthropic (`langchain-anthropic` 1.7.2): a `{"type": "thinking",
        "thinking": "..."}` block inside `message.content` -- note the text
        is under the `thinking` key, not `text`. A `{"type":
        "redacted_thinking"}` block (Anthropic's safety filtering redacted
        that segment) has no recoverable text, so it's surfaced as an
        explicit marker instead of silently dropped -- a trace reader
        should be able to tell "there was reasoning here we can't show"
        apart from "there was no reasoning at all".

    Returns `None` if nothing recognizable was found -- meaning this call's
    provider/model didn't expose anything, not that extraction failed.
    """
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue

        reasoning_content = message.additional_kwargs.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            chunks.append(reasoning_content.strip())

        if not isinstance(message.content, list):
            continue
        for block in message.content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "reasoning":
                for summary_part in block.get("summary") or []:
                    if isinstance(summary_part, dict) and summary_part.get("text"):
                        chunks.append(summary_part["text"].strip())
            elif block_type == "thinking" and block.get("thinking"):
                chunks.append(block["thinking"].strip())
            elif block_type == "redacted_thinking":
                chunks.append("[reasoning segment redacted by the provider]")

    return "\n\n".join(chunks) if chunks else None


def _extract_tool_calls(messages: list[BaseMessage]) -> list[dict]:
    """Pairs each AIMessage tool call with its corresponding ToolMessage
    result, in call order -- for a `needs_python_sandbox` question, this is
    the executed code plus its output, often the most useful part of a trace
    for diagnosing a grade."""
    tool_results_by_id = {m.tool_call_id: m.content for m in messages if isinstance(m, ToolMessage)}
    calls = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            calls.append(
                {
                    "tool": call.get("name"),
                    "args": call.get("args"),
                    "output": tool_results_by_id.get(call.get("id")),
                }
            )
    return calls


def _extract_usage(messages: list[BaseMessage]) -> tuple[int | None, int | None, int | None]:
    """Sums LangChain's standardized `usage_metadata` across every AIMessage
    in the agent run -- a `needs_python_sandbox` question may involve more
    than one LLM round-trip (tool call, then final answer), each consuming
    tokens."""
    prompt_tokens = completion_tokens = total_tokens = None
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            continue
        prompt_tokens = (prompt_tokens or 0) + (usage.get("input_tokens") or 0)
        completion_tokens = (completion_tokens or 0) + (usage.get("output_tokens") or 0)
        total_tokens = (total_tokens or 0) + (usage.get("total_tokens") or 0)
    return prompt_tokens, completion_tokens, total_tokens


def grade_answer(
    chat_model: BaseChatModel,
    question: Question,
    student_answer: str,
    grading_scale: dict[str, str],
    *,
    provider: str | None = None,
    model: str | None = None,
    on_trace: Callable[[GradeTrace], None] | None = None,
) -> Grade:
    """Grade one answer, returning just the `Grade` as before.

    `provider`/`model` and `on_trace` are new, both optional and both
    keyword-only, so every existing call site (including
    `POST /rubrics/{id}/test-answer`, which doesn't pass either) keeps
    working unchanged. When `on_trace` is given, it's called exactly once,
    on both the blank-answer short-circuit and the real-LLM path, with a
    `GradeTrace` capturing everything about the call beyond the `Grade`
    itself -- see that class's docstring. `worker/tasks.py` is the only
    current caller that passes it, to persist a `GradingTrace` document per
    question graded.
    """
    logger.debug("Grading %s: %d char(s) of answer text", question.id, len(student_answer))

    # Built unconditionally (cheap, no network) even for a blank answer, so a
    # trace always has the exact prompt that would have been sent -- useful
    # context even when no LLM call was actually made for it.
    system_prompt = build_system_prompt(question, grading_scale)

    def _emit(grade: Grade, *, blank_short_circuit: bool, elapsed: float, messages: list[BaseMessage] | None = None) -> None:
        if on_trace is None:
            return
        messages = messages or []
        prompt_tokens, completion_tokens, total_tokens = _extract_usage(messages)
        on_trace(
            GradeTrace(
                provider=provider or "unknown",
                model=model or _model_identity(chat_model),
                system_prompt=system_prompt,
                student_answer=student_answer,
                grade=grade,
                blank_short_circuit=blank_short_circuit,
                thinking=_extract_thinking(messages),
                tool_calls=_extract_tool_calls(messages),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed_seconds=elapsed,
            )
        )

    if not student_answer.strip():
        # Short-circuit blank answers instead of paying for an LLM call.
        # `grading_scale`'s first entry is, by convention, the "worst/no
        # answer" level -- see Rubric.grading_scale's docstring.
        # TODO: revisit if we ever want LLM-phrased feedback for blanks too.
        worst_level = next(iter(grading_scale))
        logger.info("%s: blank answer, skipping LLM call", question.id)
        grade = Grade(level=worst_level, feedback="No answer was provided for this question.")
        _emit(grade, blank_short_circuit=True, elapsed=0.0)
        return grade

    schema = grade_schema_for_scale(grading_scale)
    logger.debug("%s: system prompt:\n%s", question.id, system_prompt)

    logger.info("%s: calling LLM%s...", question.id, " (with python sandbox)" if question.needs_python_sandbox else "")
    start = time.perf_counter()

    tools = []
    if question.needs_python_sandbox:
        tools.append(get_python_sandbox_tool())

    result = _grade_with_tools(chat_model, tools, system_prompt, student_answer, schema)
    raw = result["structured_response"]
    messages: list[BaseMessage] = result.get("messages", [])

    # Normalize back to the stable `Grade` shape -- `raw` is an instance of
    # the one-off schema grade_schema_for_scale() just built, not `Grade`
    # itself (see that function's docstring for why the two are separate).
    grade = Grade(level=raw.level, feedback=raw.feedback)
    elapsed = time.perf_counter() - start
    logger.info("%s: graded as %s in %.2fs", question.id, grade.level, elapsed)
    logger.debug("%s feedback: %s", question.id, grade.feedback)
    _emit(grade, blank_short_circuit=False, elapsed=elapsed, messages=messages)
    return grade
