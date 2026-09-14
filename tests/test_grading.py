import pytest
from pydantic import ValidationError

from aitana.grading.grading import build_system_prompt, grade_answer
from aitana.grading.models import DEFAULT_GRADING_SCALE, Grade, Question, grade_schema_for_scale


class _FakeStructuredModel:
    def __init__(self, grade: Grade):
        self._grade = grade

    def invoke(self, messages):
        return self._grade


class _FakeChatModel:
    def __init__(self, grade: Grade):
        self._grade = grade

    def with_structured_output(self, schema):
        return _FakeStructuredModel(self._grade)


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


def test_grade_answer_returns_llm_structured_output_for_nonblank_answers():
    question = _question()
    expected = Grade(level="almost_there", feedback="Close, but missing the fix.")
    fake_model = _FakeChatModel(expected)

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
