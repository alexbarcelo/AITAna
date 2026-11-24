"""LLM-assisted segmentation (grading/segmentation.py), with create_agent faked."""

from types import SimpleNamespace

from aitana.grading import segmentation
from aitana.grading.models import AnswerSegment, Question


def _questions() -> list[Question]:
    return [
        Question(id="q1", title="First", field="Exercise 1", question="Do X", rubric="R"),
        Question(id="q2", title="Second", question="Do Y", rubric="R"),
        Question(id="q3", title="Third", question="Do Z", rubric="R"),
    ]


def _fake_create_agent(answers, captured):
    def _create_agent(*, model, tools, response_format):
        captured["response_format"] = response_format

        class _Agent:
            def invoke(self, state):
                captured["state"] = state
                raw = SimpleNamespace(
                    answers=[
                        SimpleNamespace(question_id=a[0], start_line=a[1], end_line=a[2] if len(a) > 2 else None)
                        for a in answers
                    ]
                )
                return {"structured_response": raw}

        return _Agent()

    return _create_agent


def test_suggest_segments_sanitizes_llm_output(monkeypatch):
    captured: dict = {}
    # q2 reuses q1's line, q3 is out of range -> both discarded.
    monkeypatch.setattr(segmentation, "create_agent", _fake_create_agent([("q1", 2), ("q2", 2), ("q3", 99)], captured))

    segments = segmentation.suggest_segments(object(), "a\nb\nc", _questions())

    assert segments == [
        AnswerSegment(question_id="q1", start_line=2),
        AnswerSegment(question_id="q2", start_line=None),
        AnswerSegment(question_id="q3", start_line=None),
    ]
    system, human = captured["state"]["messages"]
    assert "Label in the document: Exercise 1" in system.content
    assert human.content == "1| a\n2| b\n3| c"


def test_suggest_segments_keeps_only_sensible_end_lines(monkeypatch):
    captured: dict = {}
    # q1 ends before q2 (kept), q2 ends past q3's start (dropped), q3 ends past EOF (dropped).
    monkeypatch.setattr(
        segmentation, "create_agent", _fake_create_agent([("q1", 1, 2), ("q2", 4, 6), ("q3", 6, 99)], captured)
    )

    segments = segmentation.suggest_segments(object(), "\n".join("abcdefg"), _questions())

    assert segments == [
        AnswerSegment(question_id="q1", start_line=1, end_line=2),
        AnswerSegment(question_id="q2", start_line=4),
        AnswerSegment(question_id="q3", start_line=6),
    ]


def test_suggest_segments_drops_end_line_before_start_or_without_start(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        segmentation, "create_agent", _fake_create_agent([("q1", 3, 2), ("q2", None, 5), ("q3", 5, 7)], captured)
    )

    segments = segmentation.suggest_segments(object(), "\n".join("abcdefg"), _questions())

    assert segments == [
        AnswerSegment(question_id="q1", start_line=3),
        AnswerSegment(question_id="q2", start_line=None),
        AnswerSegment(question_id="q3", start_line=5, end_line=7),
    ]


def test_segmentation_schema_restricts_question_ids():
    schema = segmentation.segmentation_schema(_questions())
    item = schema.model_fields["answers"].annotation.__args__[0]
    assert set(item.model_fields["question_id"].annotation.__args__) == {"q1", "q2", "q3"}
