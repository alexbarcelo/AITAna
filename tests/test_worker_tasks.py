"""Regression coverage for the grading loop's incremental-save behavior.

This targets a real bug found in manual end-to-end testing: Beanie's
Document.save() re-parses the whole document and replaces `answers` with
freshly-constructed AnsweredQuestion instances on every call (validate_on_save).
A loop that captures answer references once before the loop (e.g. via
zip(questions, submission.answers)) silently drops every grade past the
first question, since later iterations mutate objects no longer referenced
by submission.answers. See _grade_answers in worker/tasks.py.
"""

from aitana.documents import Course, Edition, Rubric, Student, Submission
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
    await _grade_answers(submission, rubric.questions, object(), DEFAULT_GRADING_SCALE)

    fresh = await Submission.get(submission.id)
    assert [a.grade.level if a.grade else None for a in fresh.answers] == [
        "solid",
        "almost_there",
        "some_effort",
    ]
