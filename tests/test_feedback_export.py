"""Unit tests for feedback_export.render_feedback_html/feedback_filename --
building ORM objects in memory (never inserted), since these only render
already-loaded data.
"""

from aitana.api.feedback_export import feedback_filename, render_feedback_html
from aitana.db import AnsweredQuestion, Batch, BatchType, Course, Edition, Rubric, Student, Submission, SubmissionStatus
from aitana.grading.models import GeneralFeedback, Grade, Question, SubmissionFormat


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


async def test_render_feedback_html_includes_answer_and_grade():
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


async def test_render_feedback_html_includes_general_feedback_only_when_present():
    assert "General feedback" not in render_feedback_html(_submission())

    html_out = render_feedback_html(_submission(general_feedback=GeneralFeedback(feedback="Nice <work> overall.")))
    assert "General feedback" in html_out
    assert "Nice &lt;work&gt; overall." in html_out

    # A TA's manual general feedback wins, even with no LLM general feedback at all.
    html_out = render_feedback_html(
        _submission(general_feedback=GeneralFeedback(feedback="LLM"), manual_general_feedback="TA text")
    )
    assert "TA text" in html_out and ">LLM<" not in html_out
    assert "TA only" in render_feedback_html(_submission(manual_general_feedback="TA only"))


async def test_render_feedback_html_escapes_untrusted_content():
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


async def test_render_feedback_html_unmatched_batch_submission_uses_folder_name():
    batch = Batch(rubric=_rubric(), edition=Edition(name="2026/27", slug="2026-27"), type=BatchType.MOODLE)
    submission = _submission(
        student=None,
        batch=batch,
        batch_internal_id="Lovelace Ada_1001_assignsubmission_file",
    )
    html_out = render_feedback_html(submission)
    assert "Lovelace Ada_1001_assignsubmission_file" in html_out


async def test_render_feedback_html_no_answers_says_so():
    submission = _submission(answers=[], status=SubmissionStatus.GRADED)
    html_out = render_feedback_html(submission)
    assert "No answers extracted yet." in html_out


async def test_render_feedback_html_in_progress_shows_notice_not_empty_message():
    submission = _submission(answers=[], status=SubmissionStatus.PENDING)
    html_out = render_feedback_html(submission)
    assert "still in progress" in html_out
    assert "No answers extracted yet." not in html_out


async def test_render_feedback_html_failed_shows_error():
    submission = _submission(answers=[], status=SubmissionStatus.FAILED, error="Missing credentials")
    html_out = render_feedback_html(submission)
    assert "Grading failed" in html_out
    assert "Missing credentials" in html_out


async def test_feedback_filename_prefers_student_id_over_batch_folder():
    submission = _submission()
    assert feedback_filename(submission) == "s1_containers_feedback.html"


async def test_render_feedback_html_prefers_manual_grade():
    submission = _submission(
        answers=[
            AnsweredQuestion(
                question_id="answer1",
                student_answer="It crashed.",
                grade=Grade(level="some_effort", feedback="LLM feedback text."),
                manual_grade=Grade(level="solid", feedback="TA feedback text."),
            )
        ]
    )
    html_out = render_feedback_html(submission)

    assert "TA feedback text." in html_out
    assert "Solid" in html_out
    assert "LLM feedback text." not in html_out


async def test_render_feedback_html_embeds_images_as_data_uris():
    from aitana.grading.models import ExtractedImage

    images = {"answer1": [ExtractedImage(mime_type="image/png", data="iVBORw0KGgo=")]}
    html_out = render_feedback_html(_submission(), images)

    assert '<img src="data:image/png;base64,iVBORw0KGgo=" alt="Figure 1">' in html_out
    assert '<div class="figures">' not in render_feedback_html(_submission())


async def test_render_feedback_html_includes_text_outputs():
    answer = AnsweredQuestion(question_id="answer1", student_answer="print(1)\n--- output ---\n1")
    html_out = render_feedback_html(_submission(answers=[answer]))
    assert "--- output ---\n1" in html_out


async def test_load_answer_images_only_extracts_when_needed(monkeypatch):
    from aitana.api import feedback_export
    from aitana.grading.models import ExtractedImage, ExtractionResult

    calls = []
    image = ExtractedImage(mime_type="image/png", data="AAAA")

    def _fake_extract(submission):
        calls.append(submission)
        return ExtractionResult(images={"answer1": [image]})

    monkeypatch.setattr(feedback_export, "extract_submission_file", _fake_extract)

    assert feedback_export.load_answer_images(_submission()) == {}
    assert calls == []  # no question with include_images: no download

    # Keyed off the rubric's current toggle, not the stored image_count: an
    # answer graded before include_images was turned on still gets figures.
    rubric = _rubric(
        format=SubmissionFormat.NOTEBOOK,
        questions=[Question(id="answer1", title="Q1", question="Plot it", rubric="R", include_images=True)],
    )
    answer = AnsweredQuestion(question_id="answer1", student_answer="plot()", image_count=0)
    assert feedback_export.load_answer_images(_submission(rubric=rubric, answers=[answer])) == {"answer1": [image]}


async def test_load_answer_images_failure_returns_empty(monkeypatch):
    from aitana.api import feedback_export

    def _boom(submission):
        raise RuntimeError("object not found")

    monkeypatch.setattr(feedback_export, "extract_submission_file", _boom)
    rubric = _rubric(
        format=SubmissionFormat.NOTEBOOK,
        questions=[Question(id="answer1", title="Q1", question="Plot it", rubric="R", include_images=True)],
    )
    answer = AnsweredQuestion(question_id="answer1", student_answer="plot()", image_count=1)
    assert feedback_export.load_answer_images(_submission(rubric=rubric, answers=[answer])) == {}


async def test_render_feedback_html_header_shows_course():
    html_out = render_feedback_html(_submission(), course="BDM")
    assert "s1 &middot; BDM &middot; 2026/27" in html_out


async def test_course_name(db):
    from aitana.api.feedback_export import course_name

    rubric = _rubric()
    db.add(rubric)
    await db.flush()
    assert await course_name(db, rubric) == "BDM"


async def test_render_feedback_html_shows_question_and_labels():
    html_out = render_feedback_html(_submission())
    question = html_out.index('<p class="question-text">What happened?</p>')
    answer = html_out.index("Answer:")
    feedback = html_out.index("Feedback:")
    assert question < answer < html_out.index("It crashed because") < feedback
    assert feedback < html_out.index("Correctly identifies the OOM kill.")
