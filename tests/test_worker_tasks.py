"""The worker's grading loop and task bodies, against the real database.

`_grade_answers` writes each grade *in place* into the `answers` JSONB list
and commits after every question; the ORM can't see in-place changes to a
JSONB value on its own (`PydanticJSONB`), so
`test_grade_answers_persists_every_question` is the regression test for the
`flag_modified` call that makes those writes stick.
"""

import pytest
from sqlalchemy import select

from aitana.db import AnsweredQuestion, Course, Edition, GradingTrace, LlmModel, Rubric, Student, Submission, SubmissionStatus
from aitana.grading.models import (
    DEFAULT_GRADING_SCALE,
    ExtractionResult,
    Grade,
    GradingLevel,
    Question,
    SubmissionFormat,
)
from aitana.worker import tasks
from aitana.worker.tasks import _grade_answers

from .factories import add, reload


def _fake_create_agent(grades: list[Grade]):
    """Builds a fake `create_agent` replacement (see grading._grade_with_tools)
    that pops from a *shared* queue. grade_answer() calls create_agent() fresh
    for every question, so each call's agent.invoke() must consume the next
    grade in order, not restart from the first.
    """
    remaining = list(grades)

    class _FakeCompiledAgent:
        def invoke(self, state):
            return {"structured_response": remaining.pop(0)}

    def _create_agent(*, model, tools, response_format):
        return _FakeCompiledAgent()

    return _create_agent


async def _setup(db, questions: list[Question], answers: list[AnsweredQuestion], **rubric_fields) -> Submission:
    course = await add(db, Course(name="BDM", slug="bdm"))
    edition = await add(db, Edition(name="2026/27", slug="2026_27"))
    student = await add(db, Student(student_id="s1", name="Ada"))
    rubric = await add(db, Rubric(slug="containers", title="Containers", course=course, questions=questions, **rubric_fields))
    return await add(
        db, Submission(student=student, rubric=rubric, edition=edition, file_object_key="k", answers=answers)
    )


async def _traces(db) -> list[GradingTrace]:
    return list(await db.scalars(select(GradingTrace).order_by(GradingTrace.id)))


def _q(qid: str, **fields) -> Question:
    return Question(id=qid, title=qid.upper(), question=f"{qid}?", rubric=f"R {qid}", **fields)


async def test_grade_answers_persists_every_question(db, monkeypatch):
    submission = await _setup(
        db,
        [_q("answer1"), _q("answer2"), _q("answer3")],
        [AnsweredQuestion(question_id=f"answer{i}", student_answer=f"a{i}") for i in (1, 2, 3)],
    )
    grades = [
        Grade(level="solid", feedback="f1"),
        Grade(level="almost_there", feedback="f2"),
        Grade(level="some_effort", feedback="f3"),
    ]
    monkeypatch.setattr("aitana.grading.grading.create_agent", _fake_create_agent(grades))
    await _grade_answers(
        db, submission, submission.rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "gpt-4o-mini"
    )

    fresh = await reload(db, Submission, submission.id)
    assert [a.grade.level if a.grade else None for a in fresh.answers] == ["solid", "almost_there", "some_effort"]

    traces = await _traces(db)
    assert [t.question_id for t in traces] == ["answer1", "answer2", "answer3"]
    assert all(t.submission_id == submission.id for t in traces)
    assert [t.trace.grade.level for t in traces] == ["solid", "almost_there", "some_effort"]
    assert all(t.trace.provider == "openai" and t.trace.model == "gpt-4o-mini" for t in traces)
    assert all(a.grading_info and a.grading_info["model"] == "gpt-4o-mini" for a in fresh.answers)
    assert all(a.grading_info["reasoning_effort"] is None for a in fresh.answers)


async def test_grade_answers_records_reasoning_effort(db, monkeypatch):
    """The grading model's effort (LlmModel.reasoning_effort, passed in by
    _grade_submission) lands in both the answer's audit info and the trace."""
    submission = await _setup(db, [_q("q1")], [AnsweredQuestion(question_id="q1", student_answer="a")])

    monkeypatch.setattr("aitana.grading.grading.create_agent", _fake_create_agent([Grade(level="solid", feedback="f")]))
    await _grade_answers(
        db, submission, submission.rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "gpt-5",
        reasoning_effort="high",
    )

    fresh = await reload(db, Submission, submission.id)
    assert fresh.answers[0].grading_info["reasoning_effort"] == "high"
    [trace] = await _traces(db)
    assert trace.trace.info["reasoning_effort"] == "high"


async def test_grade_answers_blank_answer_gets_the_zero_level(db, monkeypatch):
    """No LLM call for a blank answer: it gets the scale's score-0 level,
    wherever that level sits in the list."""
    scale = [
        GradingLevel(id="great", description="Great", score=2),
        GradingLevel(id="nothing", description="Nothing", score=0),
        GradingLevel(id="wrong", description="Wrong", score=-1),
    ]
    submission = await _setup(
        db, [_q("q1")], [AnsweredQuestion(question_id="q1", student_answer="   ")], grading_scale=scale
    )

    def _no_llm(**kwargs):
        raise AssertionError("blank answers must not reach the LLM")

    monkeypatch.setattr("aitana.grading.grading.create_agent", _no_llm)
    await _grade_answers(db, submission, submission.rubric.questions, object(), submission.rubric.grading_scale, "o", "m")

    fresh = await reload(db, Submission, submission.id)
    assert fresh.answers[0].grade.level == "nothing"
    [trace] = await _traces(db)
    assert trace.trace.blank_short_circuit is True


def test_rebuild_answers_carries_over_manual_overrides():
    from datetime import UTC, datetime

    from aitana.worker.tasks import _rebuild_answers

    when = datetime(2026, 10, 1, tzinfo=UTC)
    questions = [_q("answer1"), _q("answer2")]
    previous = [
        AnsweredQuestion(
            question_id="answer1",
            student_answer="old",
            grade=Grade(level="some_effort", feedback="LLM"),
            manual_grade=Grade(level="solid", feedback="TA"),
            manual_graded_at=when,
        ),
        AnsweredQuestion(question_id="answer2", student_answer="old", grade=Grade(level="solid", feedback="LLM")),
        # Question no longer in the rubric -- its override is dropped with it.
        AnsweredQuestion(question_id="gone", student_answer="x", manual_grade=Grade(level="solid", feedback="TA")),
    ]

    answers = _rebuild_answers(
        questions, ExtractionResult(answers={"answer1": "new1", "answer2": "new2"}, untagged_cells={"answer2": 3}), previous
    )

    assert [a.question_id for a in answers] == ["answer1", "answer2"]
    assert answers[0].student_answer == "new1"
    assert answers[0].grade is None  # LLM grade is refreshed by the re-grade
    assert answers[0].manual_grade == Grade(level="solid", feedback="TA")
    assert answers[0].manual_graded_at == when
    assert answers[1].manual_grade is None
    assert [a.untagged_cells for a in answers] == [0, 3]


def test_rebuild_answers_keeps_other_grades_on_single_question_regrade():
    from aitana.worker.tasks import _rebuild_answers

    previous = [
        AnsweredQuestion(
            question_id="answer1", student_answer="a1", grade=Grade(level="solid", feedback="f1"), grading_info={"model": "m"}
        ),
        AnsweredQuestion(
            question_id="answer2", student_answer="a2", grade=Grade(level="solid", feedback="f2"), grading_info={"model": "m"}
        ),
    ]
    answers = _rebuild_answers(
        [_q("answer1"), _q("answer2")],
        ExtractionResult(answers={"answer1": "a1", "answer2": "a2"}),
        previous,
        keep_grades_except="answer2",
    )
    assert answers[0].grade == Grade(level="solid", feedback="f1")
    assert answers[0].grading_info == {"model": "m"}
    assert answers[1].grade is None
    assert answers[1].grading_info is None


async def test_grade_answers_only_question_id(db, monkeypatch):
    old = Grade(level="solid", feedback="old")
    submission = await _setup(
        db,
        [_q("answer1"), _q("answer2")],
        [
            AnsweredQuestion(question_id="answer1", student_answer="a1", grade=old),
            AnsweredQuestion(question_id="answer2", student_answer="a2"),
        ],
    )

    monkeypatch.setattr(
        "aitana.grading.grading.create_agent", _fake_create_agent([Grade(level="some_effort", feedback="new")])
    )
    await _grade_answers(
        db, submission, submission.rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "m",
        only_question_id="answer2",
    )

    fresh = await reload(db, Submission, submission.id)
    assert [a.grade for a in fresh.answers] == [old, Grade(level="some_effort", feedback="new")]
    assert [t.question_id for t in await _traces(db)] == ["answer2"]


def test_rebuild_answers_uses_outputs_and_counts_images_only_when_enabled():
    from aitana.grading.models import ExtractedImage
    from aitana.worker.tasks import _rebuild_answers

    questions = [_q("answer1"), _q("answer2", include_outputs=True, include_images=True)]
    image = ExtractedImage(mime_type="image/png", data="AAAA")
    extracted = ExtractionResult(
        answers={"answer1": "code1", "answer2": "code2"},
        answers_with_outputs={"answer1": "code1\n--- output ---\n1", "answer2": "code2\n--- output ---\n2"},
        images={"answer1": [image], "answer2": [image, image]},
    )

    answers = _rebuild_answers(questions, extracted, [])

    assert [a.student_answer for a in answers] == ["code1", "code2\n--- output ---\n2"]
    assert [a.image_count for a in answers] == [0, 2]


async def test_grade_answers_forwards_images_only_for_include_images(db, monkeypatch):
    from aitana.grading.models import ExtractedImage

    submission = await _setup(
        db,
        [_q("answer1"), _q("answer2", include_images=True)],
        [
            AnsweredQuestion(question_id="answer1", student_answer="a1"),
            AnsweredQuestion(question_id="answer2", student_answer="a2"),
        ],
        format=SubmissionFormat.NOTEBOOK,
    )

    seen: list = []
    remaining = [Grade(level="solid", feedback="1"), Grade(level="solid", feedback="2")]

    class _Agent:
        def invoke(self, state):
            seen.append(state["messages"][1].content)
            return {"structured_response": remaining.pop(0)}

    monkeypatch.setattr("aitana.grading.grading.create_agent", lambda **kwargs: _Agent())
    image = ExtractedImage(mime_type="image/png", data="AAAA")
    await _grade_answers(
        db,
        submission,
        submission.rubric.questions,
        object(),
        DEFAULT_GRADING_SCALE,
        "openai",
        "m",
        images={"answer1": [image], "answer2": [image]},
    )

    assert seen[0] == "a1"  # include_images off: plain text, image ignored
    assert seen[1][1] == {"type": "image", "base64": "AAAA", "mime_type": "image/png"}
    assert sorted(t.trace.image_count for t in await _traces(db)) == [0, 1]


# ---- _grade_submission end to end ----


async def _setup_pending(db, monkeypatch, extracted: ExtractionResult, **rubric_fields) -> Submission:
    """A pending notebook submission, with extraction and the chat model
    faked out and a default model to resolve."""
    db.add(LlmModel(provider="openai", model="gpt-4o-mini", is_default=True))
    submission = await _setup(
        db, [_q("answer1"), _q("answer2")], [], format=SubmissionFormat.NOTEBOOK, **rubric_fields
    )
    monkeypatch.setattr(tasks, "extract_submission_file", lambda s: extracted)
    monkeypatch.setattr(tasks, "get_chat_model", lambda *args, **kwargs: object())
    return submission


async def test_grade_submission_extracts_grades_and_finishes(db, monkeypatch):
    submission = await _setup_pending(db, monkeypatch, ExtractionResult(answers={"answer1": "x", "answer2": "y"}))
    monkeypatch.setattr(
        "aitana.grading.grading.create_agent",
        _fake_create_agent([Grade(level="solid", feedback="1"), Grade(level="some_effort", feedback="2")]),
    )
    db.expunge_all()

    await tasks._grade_submission(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.GRADED
    assert fresh.error is None
    assert [(a.question_id, a.student_answer, a.grade.level) for a in fresh.answers] == [
        ("answer1", "x", "solid"),
        ("answer2", "y", "some_effort"),
    ]
    assert len(await _traces(db)) == 2
    assert fresh.general_feedback is None  # the rubric has no general_feedback_instructions


def _fake_general_feedback_agent(seen: list, feedback: str = "Good work overall."):
    from types import SimpleNamespace

    class _Agent:
        def invoke(self, state):
            seen.append(state["messages"])
            return {"structured_response": SimpleNamespace(feedback=feedback)}

    def _create_agent(*, model, tools, response_format):
        return _Agent()

    return _create_agent


async def test_grade_submission_writes_general_feedback_from_effective_grades(db, monkeypatch):
    submission = await _setup_pending(
        db,
        monkeypatch,
        ExtractionResult(answers={"answer1": "x", "answer2": "y"}),
        general_feedback_instructions="Summarize in one sentence.",
        context="Docker lab.",
    )
    # A TA override on answer2 survives the re-grade and is what the
    # general feedback sees.
    submission.answers = [
        AnsweredQuestion(question_id="answer2", student_answer="y", manual_grade=Grade(level="solid", feedback="TA says fine"))
    ]
    await db.commit()
    monkeypatch.setattr(
        "aitana.grading.grading.create_agent",
        _fake_create_agent([Grade(level="solid", feedback="f1"), Grade(level="some_effort", feedback="f2")]),
    )
    seen: list = []
    monkeypatch.setattr("aitana.grading.general_feedback.create_agent", _fake_general_feedback_agent(seen))
    db.expunge_all()

    await tasks._grade_submission(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.GRADED
    assert fresh.error is None
    assert fresh.general_feedback.feedback == "Good work overall."
    assert fresh.general_feedback.info["model"] == "gpt-4o-mini"
    [[system, human]] = seen
    assert "Summarize in one sentence." in system.content
    assert "Docker lab." in system.content
    assert "- solid:" in system.content  # the grading scale
    assert "## ANSWER1\nGrade: solid\nFeedback: f1" in human.content
    assert "## ANSWER2\nGrade: solid\nFeedback: TA says fine" in human.content


async def test_regenerate_general_feedback_uses_current_grades_only(db, monkeypatch):
    """Rewrites only the general feedback, from the effective grades, keeps the TA's
    manual general feedback, and puts the submission back to graded."""
    db.add(LlmModel(provider="openai", model="gpt-4o-mini", is_default=True))
    submission = await _setup(
        db,
        [_q("answer1")],
        [
            AnsweredQuestion(
                question_id="answer1",
                student_answer="x",
                grade=Grade(level="some_effort", feedback="LLM"),
                manual_grade=Grade(level="solid", feedback="TA override"),
            )
        ],
        general_feedback_instructions="Summarize.",
    )
    submission.status = SubmissionStatus.GRADING
    submission.manual_general_feedback = "TA general feedback"
    await db.commit()
    monkeypatch.setattr(tasks, "get_chat_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(tasks, "extract_submission_file", lambda s: pytest.fail("must not re-extract"))
    monkeypatch.setattr("aitana.grading.grading.create_agent", lambda **kw: pytest.fail("must not re-grade"))
    seen: list = []
    monkeypatch.setattr("aitana.grading.general_feedback.create_agent", _fake_general_feedback_agent(seen, "Fresh general feedback."))
    db.expunge_all()

    await tasks._regenerate_general_feedback(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.GRADED
    assert fresh.general_feedback.feedback == "Fresh general feedback."
    assert fresh.manual_general_feedback == "TA general feedback"
    assert fresh.answers[0].grade.feedback == "LLM"
    [[_system, human]] = seen
    assert "Grade: solid\nFeedback: TA override" in human.content


async def test_regenerate_general_feedback_failure_still_ends_graded(db, monkeypatch):
    db.add(LlmModel(provider="openai", model="gpt-4o-mini", is_default=True))
    submission = await _setup(
        db, [_q("answer1")], [AnsweredQuestion(question_id="answer1", student_answer="x")], general_feedback_instructions="S."
    )
    submission.status = SubmissionStatus.GRADING
    await db.commit()

    def _broken(*args, **kwargs):
        raise RuntimeError("no key")

    monkeypatch.setattr(tasks, "get_chat_model", _broken)
    db.expunge_all()

    await tasks._regenerate_general_feedback(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.GRADED
    assert "General feedback failed: no key" in fresh.error


async def test_grade_submission_general_feedback_failure_is_not_fatal(db, monkeypatch):
    submission = await _setup_pending(
        db, monkeypatch, ExtractionResult(answers={"answer1": "x", "answer2": "y"}), general_feedback_instructions="Summarize."
    )
    monkeypatch.setattr(
        "aitana.grading.grading.create_agent",
        _fake_create_agent([Grade(level="solid", feedback="1"), Grade(level="solid", feedback="2")]),
    )

    def _broken(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("aitana.grading.general_feedback.create_agent", _broken)
    db.expunge_all()

    await tasks._grade_submission(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.GRADED
    assert [a.grade.level for a in fresh.answers] == ["solid", "solid"]
    assert fresh.general_feedback is None
    assert "General feedback failed: provider down" in fresh.error


async def test_grade_submission_failure_keeps_extracted_answers(db, monkeypatch):
    """A broken provider fails the submission, but the extracted answers
    were committed before the LLM step and stay visible."""
    submission = await _setup_pending(db, monkeypatch, ExtractionResult(answers={"answer1": "x", "answer2": "y"}))

    def _broken(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(tasks, "get_chat_model", _broken)
    db.expunge_all()

    await tasks._grade_submission(db, submission.id)

    fresh = await reload(db, Submission, submission.id)
    assert fresh.status == SubmissionStatus.FAILED
    assert "OPENAI_API_KEY" in fresh.error
    assert [a.student_answer for a in fresh.answers] == ["x", "y"]


async def test_grade_submission_stops_when_the_row_is_deleted(db, monkeypatch):
    """A forced delete mid-run (`DELETE /submissions/{id}?force=true`): the
    task's next commit matches no row, so it stops with `SubmissionGone`
    (which `_run_grade_submission` logs) instead of failing or writing
    anything."""
    from sqlalchemy import delete

    submission = await _setup_pending(db, monkeypatch, ExtractionResult(answers={"answer1": "x", "answer2": "y"}))
    submission_id = submission.id
    deleted = False
    real_commit = tasks._commit

    async def _commit_racing_a_delete(session):
        # After the first status update, the row disappears "from another
        # connection": a Core DELETE the session's identity map knows nothing about.
        nonlocal deleted
        if not deleted:
            await real_commit(session)
            await session.execute(
                delete(Submission).where(Submission.id == submission_id), execution_options={"synchronize_session": False}
            )
            deleted = True
            return
        await real_commit(session)

    monkeypatch.setattr(tasks, "_commit", _commit_racing_a_delete)
    db.expunge_all()

    with pytest.raises(tasks.SubmissionGone):
        await tasks._grade_submission(db, submission_id)
    # (The DELETE itself is rolled back here -- in this test it shares the
    # task's transaction -- so only the task's reaction is asserted.)
    assert await _traces(db) == []


async def test_extract_segmented_submission_reads_stored_markdown_and_images(monkeypatch):
    """Generic documents are sliced from the stored Markdown + segments, and only
    images of `include_images` questions get downloaded."""
    import base64

    from aitana.grading.models import AnswerSegment, ConvertedDocument, DocumentImage
    from aitana.worker.tasks import _extract_segmented_submission

    objects = {
        "doc.md": b"# Ex 1\n![](img-0001.png)\n# Ex 2\n![](img-0002.png)",
        "img1": b"png-1",
        "img2": b"png-2",
    }
    downloaded: list[str] = []

    def fake_download(key: str) -> bytes:
        downloaded.append(key)
        return objects[key]

    monkeypatch.setattr("aitana.storage.download_bytes", fake_download)

    rubric = Rubric(
        slug="free",
        title="Free",
        course=Course(name="BDM", slug="bdm"),
        format=SubmissionFormat.GENERIC_DOCUMENT,
        questions=[_q("q1", include_images=True), _q("q2")],
    )
    submission = Submission(
        rubric=rubric,
        edition=Edition(name="2026/27", slug="2026_27"),
        file_object_key="k.pdf",
        document=ConvertedDocument(
            markdown_object_key="doc.md",
            line_count=4,
            images=[
                DocumentImage(name="img-0001.png", object_key="img1", line=2, page=1, mime_type="image/png"),
                DocumentImage(name="img-0002.png", object_key="img2", line=4, page=1, mime_type="image/png"),
            ],
        ),
        segments=[AnswerSegment(question_id="q1", start_line=1), AnswerSegment(question_id="q2", start_line=3)],
    )

    result = _extract_segmented_submission(submission)

    assert result.answers == {"q1": "# Ex 1\n![](img-0001.png)", "q2": "# Ex 2\n![](img-0002.png)"}
    assert [img.data for img in result.images["q1"]] == [base64.b64encode(b"png-1").decode()]
    assert "q2" not in result.images
    assert downloaded == ["doc.md", "img1"]


async def test_prepare_converts_pdf_and_waits_for_segmentation(db, monkeypatch):
    import pymupdf

    from aitana.worker.tasks import _prepare

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Exercise 1: my answer")
    pdf_bytes = doc.tobytes()
    stored: dict[str, bytes] = {"r/s/1.pdf": pdf_bytes}
    monkeypatch.setattr("aitana.storage.download_to_path", lambda key, dest: dest.write_bytes(stored[key]))
    monkeypatch.setattr("aitana.storage.upload_file", lambda key, data, content_type: stored.__setitem__(key, data))

    course = await add(db, Course(name="BDM", slug="bdm"))
    rubric = await add(db, Rubric(slug="free", title="Free", course=course, format=SubmissionFormat.GENERIC_DOCUMENT, questions=[]))
    edition = await add(db, Edition(name="2026/27", slug="2026_27"))
    submission = await add(db, Submission(rubric=rubric, edition=edition, file_object_key="r/s/1.pdf"))

    await _prepare(db, submission, None)

    saved = await reload(db, Submission, submission.id)
    assert saved.status == SubmissionStatus.AWAITING_SEGMENTATION
    assert saved.document.markdown_object_key == "r/s/1/document.md"
    assert "Exercise 1: my answer" in stored["r/s/1/document.md"].decode()
    assert saved.document.page_start_lines == [1]
    assert saved.segments is None
