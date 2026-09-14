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


class _FakeStructuredModel:
    def __init__(self, chat_model: "_FakeChatModel"):
        self._chat_model = chat_model

    def invoke(self, messages):
        # Pops from the *shared* queue on the chat model -- grade_answer()
        # calls with_structured_output() fresh for every question, so each
        # call must consume the next grade in order, not restart from the
        # first (a fresh `iter(grades)` per call would return "solid" three
        # times instead of solid/almost_there/some_effort).
        return self._chat_model._grades.pop(0)


class _FakeChatModel:
    def __init__(self, grades: list[Grade]):
        self._grades = list(grades)

    def with_structured_output(self, schema):
        return _FakeStructuredModel(self)


async def test_grade_answers_persists_every_question_not_just_the_first(mongo_db):
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
    await _grade_answers(submission, rubric.questions, _FakeChatModel(grades), DEFAULT_GRADING_SCALE)

    fresh = await Submission.get(submission.id)
    assert [a.grade.level if a.grade else None for a in fresh.answers] == [
        "solid",
        "almost_there",
        "some_effort",
    ]
