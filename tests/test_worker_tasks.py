"""Regression coverage for the grading loop's incremental-save behavior.

This targets a real bug found in manual end-to-end testing: Beanie's
Document.save() re-parses the whole document and replaces `answers` with
freshly-constructed AnsweredQuestion instances on every call (validate_on_save).
A loop that captures answer references once before the loop (e.g. via
zip(questions, submission.answers)) silently drops every grade past the
first question, since later iterations mutate objects no longer referenced
by submission.answers. See _grade_answers in worker/tasks.py.
"""

from aitana.documents import Course, Edition, GradingTrace, Rubric, Student, Submission
from aitana.documents.submission import AnsweredQuestion
from aitana.grading.models import DEFAULT_GRADING_SCALE, Grade, Question
from aitana.worker.tasks import _grade_answers


def _fake_create_agent(grades: list[Grade]):
    """Builds a fake `create_agent` replacement (see grading._grade_with_tools)
    that pops from a *shared* queue. grade_answer() calls create_agent() fresh
    for every question, so each call's agent.invoke() must consume the next
    grade in order, not restart from the first (a fresh `iter(grades)` per
    call would return "solid" three times instead of solid/almost_there/some_effort).
    """
    remaining = list(grades)

    class _FakeCompiledAgent:
        def invoke(self, state):
            return {"structured_response": remaining.pop(0)}

    def _create_agent(*, model, tools, response_format):
        return _FakeCompiledAgent()

    return _create_agent


async def test_grade_answers_persists_every_question_not_just_the_first(mongo_db, monkeypatch):
    course = Course(name="BDM", slug="bdm")
    await course.insert()
    edition = Edition(course=course, name="2026/27", slug="bdm_2026_27")
    await edition.insert()
    student = Student(student_id="s1", name="Ada")
    await student.insert()
    rubric = Rubric(
        slug="containers",
        title="Containers",
        course=course,
        questions=[
            Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
            Question(id="answer2", title="Q2", question="Q2?", rubric="R2"),
            Question(id="answer3", title="Q3", question="Q3?", rubric="R3"),
        ],
    )
    await rubric.insert()
    submission = Submission(
        student=student,
        rubric=rubric,
        edition=edition,
        file_object_key="k",
        answers=[
            AnsweredQuestion(question_id="answer1", student_answer="a1"),
            AnsweredQuestion(question_id="answer2", student_answer="a2"),
            AnsweredQuestion(question_id="answer3", student_answer="a3"),
        ],
    )
    await submission.insert()

    grades = [
        Grade(level="solid", feedback="f1"),
        Grade(level="almost_there", feedback="f2"),
        Grade(level="some_effort", feedback="f3"),
    ]
    monkeypatch.setattr("aitana.grading.grading.create_agent", _fake_create_agent(grades))
    await _grade_answers(submission, rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "gpt-4o-mini")

    fresh = await Submission.get(submission.id)
    assert [a.grade.level if a.grade else None for a in fresh.answers] == [
        "solid",
        "almost_there",
        "some_effort",
    ]

    traces = await GradingTrace.find_all().to_list()
    assert sorted(t.question_id for t in traces) == ["answer1", "answer2", "answer3"]
    assert {t.trace.grade.level for t in traces} == {"solid", "almost_there", "some_effort"}
    assert all(t.trace.provider == "openai" and t.trace.model == "gpt-4o-mini" for t in traces)
    assert all(a.grading_info and a.grading_info["model"] == "gpt-4o-mini" for a in fresh.answers)
    assert all(a.grading_info["reasoning_effort"] is None for a in fresh.answers)


async def test_grade_answers_records_reasoning_effort(mongo_db, monkeypatch):
    """The grading model's effort (LlmModel.reasoning_effort, passed in by
    _grade_submission) lands in both the answer's audit info and the trace."""
    course = Course(name="BDM", slug="bdm")
    await course.insert()
    edition = Edition(name="2026/27", slug="2026_27")
    await edition.insert()
    rubric = Rubric(slug="lab", title="Lab", course=course, questions=[Question(id="q1", title="Q1", question="?", rubric="R")])
    await rubric.insert()
    submission = Submission(
        rubric=rubric, edition=edition, file_object_key="k", answers=[AnsweredQuestion(question_id="q1", student_answer="a")]
    )
    await submission.insert()

    monkeypatch.setattr("aitana.grading.grading.create_agent", _fake_create_agent([Grade(level="solid", feedback="f")]))
    await _grade_answers(
        submission, rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "gpt-5", reasoning_effort="high"
    )

    fresh = await Submission.get(submission.id)
    assert fresh.answers[0].grading_info["reasoning_effort"] == "high"
    [trace] = await GradingTrace.find_all().to_list()
    assert trace.trace.info["reasoning_effort"] == "high"


def test_rebuild_answers_carries_over_manual_overrides(mongo_db):
    from datetime import UTC, datetime

    from aitana.grading.models import ExtractionResult
    from aitana.worker.tasks import _rebuild_answers

    when = datetime(2026, 10, 1, tzinfo=UTC)
    questions = [
        Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
        Question(id="answer2", title="Q2", question="Q2?", rubric="R2"),
    ]
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


def test_rebuild_answers_keeps_other_grades_on_single_question_regrade(mongo_db):
    from aitana.grading.models import ExtractionResult
    from aitana.worker.tasks import _rebuild_answers

    questions = [
        Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
        Question(id="answer2", title="Q2", question="Q2?", rubric="R2"),
    ]
    previous = [
        AnsweredQuestion(
            question_id="answer1", student_answer="a1", grade=Grade(level="solid", feedback="f1"), grading_info={"model": "m"}
        ),
        AnsweredQuestion(
            question_id="answer2", student_answer="a2", grade=Grade(level="solid", feedback="f2"), grading_info={"model": "m"}
        ),
    ]
    answers = _rebuild_answers(
        questions, ExtractionResult(answers={"answer1": "a1", "answer2": "a2"}), previous, keep_grades_except="answer2"
    )
    assert answers[0].grade == Grade(level="solid", feedback="f1")
    assert answers[0].grading_info == {"model": "m"}
    assert answers[1].grade is None
    assert answers[1].grading_info is None


async def test_grade_answers_only_question_id(mongo_db, monkeypatch):
    course = Course(name="BDM", slug="bdm")
    await course.insert()
    edition = Edition(name="2026/27", slug="2026_27")
    await edition.insert()
    rubric = Rubric(
        slug="containers",
        title="Containers",
        course=course,
        questions=[
            Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
            Question(id="answer2", title="Q2", question="Q2?", rubric="R2"),
        ],
    )
    await rubric.insert()
    old = Grade(level="solid", feedback="old")
    submission = Submission(
        rubric=rubric,
        edition=edition,
        file_object_key="k",
        answers=[
            AnsweredQuestion(question_id="answer1", student_answer="a1", grade=old),
            AnsweredQuestion(question_id="answer2", student_answer="a2"),
        ],
    )
    await submission.insert()

    monkeypatch.setattr(
        "aitana.grading.grading.create_agent", _fake_create_agent([Grade(level="some_effort", feedback="new")])
    )
    await _grade_answers(
        submission, rubric.questions, object(), DEFAULT_GRADING_SCALE, "openai", "m", only_question_id="answer2"
    )

    fresh = await Submission.get(submission.id)
    assert [a.grade for a in fresh.answers] == [old, Grade(level="some_effort", feedback="new")]
    assert [t.question_id for t in await GradingTrace.find_all().to_list()] == ["answer2"]


def test_rebuild_answers_uses_outputs_and_counts_images_only_when_enabled(mongo_db):
    from aitana.grading.models import ExtractedImage, ExtractionResult
    from aitana.worker.tasks import _rebuild_answers

    questions = [
        Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
        Question(id="answer2", title="Q2", question="Q2?", rubric="R2", include_outputs=True, include_images=True),
    ]
    image = ExtractedImage(mime_type="image/png", data="AAAA")
    extracted = ExtractionResult(
        answers={"answer1": "code1", "answer2": "code2"},
        answers_with_outputs={"answer1": "code1\n--- output ---\n1", "answer2": "code2\n--- output ---\n2"},
        images={"answer1": [image], "answer2": [image, image]},
    )

    answers = _rebuild_answers(questions, extracted, [])

    assert [a.student_answer for a in answers] == ["code1", "code2\n--- output ---\n2"]
    assert [a.image_count for a in answers] == [0, 2]


async def test_grade_answers_forwards_images_only_for_include_images(mongo_db, monkeypatch):
    from aitana.grading.models import ExtractedImage

    course = Course(name="BDM", slug="bdm")
    await course.insert()
    edition = Edition(name="2026/27", slug="2026_27")
    await edition.insert()
    rubric = Rubric(
        slug="figures",
        title="Figures",
        course=course,
        format="notebook",
        questions=[
            Question(id="answer1", title="Q1", question="Q1?", rubric="R1"),
            Question(id="answer2", title="Q2", question="Q2?", rubric="R2", include_images=True),
        ],
    )
    await rubric.insert()
    submission = Submission(
        rubric=rubric,
        edition=edition,
        file_object_key="k",
        answers=[
            AnsweredQuestion(question_id="answer1", student_answer="a1"),
            AnsweredQuestion(question_id="answer2", student_answer="a2"),
        ],
    )
    await submission.insert()

    seen: list = []
    remaining = [Grade(level="solid", feedback="1"), Grade(level="solid", feedback="2")]

    class _Agent:
        def invoke(self, state):
            seen.append(state["messages"][1].content)
            return {"structured_response": remaining.pop(0)}

    monkeypatch.setattr("aitana.grading.grading.create_agent", lambda **kwargs: _Agent())
    image = ExtractedImage(mime_type="image/png", data="AAAA")
    await _grade_answers(
        submission,
        rubric.questions,
        object(),
        DEFAULT_GRADING_SCALE,
        "openai",
        "m",
        images={"answer1": [image], "answer2": [image]},
    )

    assert seen[0] == "a1"  # include_images off: plain text, image ignored
    assert seen[1][1] == {"type": "image", "base64": "AAAA", "mime_type": "image/png"}
    traces = await GradingTrace.find_all().to_list()
    assert sorted(t.trace.image_count for t in traces) == [0, 1]
