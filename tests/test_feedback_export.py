"""Unit tests for feedback_export.render_feedback_html/feedback_filename --
constructing `Document`s directly (never inserted) since these only render
already-in-memory data. `mongo_db` is still required as a fixture: Beanie's
`Document.__init__` looks up collection settings that only exist once
`init_beanie` has run, even for a document that's never saved.
"""

from aitana.api.feedback_export import feedback_filename, render_feedback_html
from aitana.documents import Batch, BatchType, Course, Edition, Rubric, Student, Submission, SubmissionStatus
from aitana.documents.submission import AnsweredQuestion
from aitana.grading.models import Grade, Question


def _rubric(**overrides) -> Rubric:
    defaults = dict(
        slug="containers",
        title="Containers lab",
        course=Course(name="BDM", slug="bdm"),
        questions=[Question(id="answer1", title="Q1", question="What happened?", rubric="R")],
    )
    defaults.update(overrides)
    return Rubric(**defaults)


def _submission(**overrides) -> Submission:
    defaults = dict(
        rubric=_rubric(),
        edition=Edition(name="2026/27", slug="2026-27"),
        student=Student(student_id="s1", name="Ada Lovelace"),
        file_object_key="k",
        status=SubmissionStatus.GRADED,
        answers=[
            AnsweredQuestion(
                question_id="answer1",
                student_answer="It crashed because of an OOM kill.",
                grade=Grade(level="solid", feedback="Correctly identifies the OOM kill."),
            )
        ],
    )
    defaults.update(overrides)
    return Submission(**defaults)


async def test_render_feedback_html_includes_answer_and_grade(mongo_db):
    html_out = render_feedback_html(_submission())

    assert "Ada Lovelace" in html_out
    assert "Containers lab" in html_out
    assert "It crashed because of an OOM kill." in html_out
    assert "Correctly identifies the OOM kill." in html_out
    assert "Solid" in html_out  # prettified level
    # Standalone: no reference to anything outside the file itself.
    assert "http://" not in html_out and "https://" not in html_out
    assert "<link" not in html_out
    assert "<script" not in html_out


async def test_render_feedback_html_escapes_untrusted_content(mongo_db):
    submission = _submission(
        answers=[
            AnsweredQuestion(
                question_id="answer1",
                student_answer="<script>alert(1)</script>",
                grade=Grade(level="solid", feedback="ok"),
            )
        ]
    )
    html_out = render_feedback_html(submission)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


async def test_render_feedback_html_unmatched_batch_submission_uses_folder_name(mongo_db):
    batch = Batch(rubric=_rubric(), edition=Edition(name="2026/27", slug="2026-27"), type=BatchType.ATENEA)
    submission = _submission(
        student=None,
        batch=batch,
        batch_internal_id="Lovelace Ada_1001_assignsubmission_file",
    )
    html_out = render_feedback_html(submission)
    assert "Lovelace Ada_1001_assignsubmission_file" in html_out


async def test_render_feedback_html_no_answers_says_so(mongo_db):
    submission = _submission(answers=[], status=SubmissionStatus.GRADED)
    html_out = render_feedback_html(submission)
    assert "No answers extracted yet." in html_out


async def test_render_feedback_html_in_progress_shows_notice_not_empty_message(mongo_db):
    submission = _submission(answers=[], status=SubmissionStatus.PENDING)
    html_out = render_feedback_html(submission)
    assert "still in progress" in html_out
    assert "No answers extracted yet." not in html_out


async def test_render_feedback_html_failed_shows_error(mongo_db):
    submission = _submission(answers=[], status=SubmissionStatus.FAILED, error="Missing credentials")
    html_out = render_feedback_html(submission)
    assert "Grading failed" in html_out
    assert "Missing credentials" in html_out


async def test_feedback_filename_prefers_student_id_over_batch_folder(mongo_db):
    submission = _submission()
    assert feedback_filename(submission) == "s1_containers_feedback.html"
