import csv
import io

from sqlalchemy import func, select

from aitana.db import AnsweredQuestion, Course, Edition, Rubric, Student, Submission, SubmissionStatus
from aitana.grading.models import ConvertedDocument, GeneralFeedback, Grade, Question, SubmissionFormat

from .factories import add, make_edition, make_rubric, make_submission, reload, save


async def _make_student(client) -> int:
    resp = await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    return resp.json()["id"]


async def _make_course(client, name: str = "BDM") -> int:
    return (await client.post("/courses", json={"name": name})).json()["id"]


def _question(**overrides) -> Question:
    defaults = dict(id="answer1", title="Q1", question="What happened?", rubric="R")
    defaults.update(overrides)
    return Question(**defaults)


async def _make_rubric(
    client, course_id: int, edition_id: int | None = None, fmt: SubmissionFormat = SubmissionFormat.PDF_FORM
) -> int:
    db = client.db
    course = await db.get(Course, course_id)
    edition = await db.get(Edition, edition_id) if edition_id is not None else None
    rubric = Rubric(
        slug="containers", title="Containers lab", course=course, edition=edition, format=fmt, questions=[_question()]
    )
    await add(db, rubric)
    return rubric.id


async def test_create_submission_uploads_file_and_enqueues_grading(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"%PDF-1.4 fake content", "application/pdf")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["file_object_key"]

    assert client.delayed_submission_ids == [body["id"]]
    assert client.fake_objects[body["file_object_key"]] == b"%PDF-1.4 fake content"


async def test_create_labelled_submission(client):
    """Group projects: no roster student, just a free-form label."""
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/submissions",
        data={"label": "  Group 3  ", "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"%PDF-1.4 group", "application/pdf")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["student"] is None
    assert body["label"] == "Group 3"
    assert "/_labelled/" in body["file_object_key"]
    assert client.fake_objects[body["file_object_key"]] == b"%PDF-1.4 group"
    assert client.delayed_submission_ids == [body["id"]]


async def test_rename_submission_label(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    submission_id = (
        await client.post(
            "/submissions",
            data={"label": "Grup 3", "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"%PDF-1.4", "application/pdf")},
        )
    ).json()["id"]

    resp = await client.put(f"/submissions/{submission_id}/label", json={"label": " Group 3 "})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Group 3"
    assert (await reload(client.db, Submission, submission_id)).label == "Group 3"

    assert (await client.put(f"/submissions/{submission_id}/label", json={"label": "  "})).status_code == 422
    missing = "/submissions/999999999/label"
    assert (await client.put(missing, json={"label": "x"})).status_code == 404


async def test_create_submission_requires_exactly_one_of_student_or_label(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    for data in (
        {"rubric_id": rubric_id},
        {"rubric_id": rubric_id, "label": "   "},
        {"rubric_id": rubric_id, "label": "Group 3", "student_id": student_id},
    ):
        resp = await client.post("/submissions", data=data, files={"file": ("answers.pdf", b"data", "application/pdf")})
        assert resp.status_code == 422, data
    assert client.delayed_submission_ids == []


async def test_create_submission_uses_rubrics_edition_automatically(client):
    """Uploading against a rubric that has a fixed edition shouldn't need an
    explicit edition_id -- the rubric's own edition is used."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},  # no edition_id
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 202


async def test_create_submission_without_edition_requires_explicit_one(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    rubric_id = await _make_rubric(client, course_id)  # no fixed edition

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},  # no edition_id either
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 422


async def test_create_submission_explicit_edition_for_edition_agnostic_rubric(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id)  # no fixed edition

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id, "edition_id": edition_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 202


async def test_create_submission_unknown_student_404(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    resp = await client.post(
        "/submissions",
        data={"student_id": "999999999", "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 404


async def test_create_submission_accepts_notebook_file_for_notebook_rubric(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.NOTEBOOK)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("submission.ipynb", b'{"cells": []}', "application/x-ipynb+json")},
    )
    assert resp.status_code == 202
    assert resp.json()["file_object_key"].endswith(".ipynb")


async def test_create_submission_rejects_file_extension_mismatching_rubric_format(client):
    """A PDF-format rubric should reject an obviously-wrong file (e.g. a
    notebook) at upload time with a 422, rather than letting it fail
    confusingly once grading starts."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)  # defaults to pdf

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("submission.ipynb", b'{"cells": []}', "application/x-ipynb+json")},
    )
    assert resp.status_code == 422


async def test_list_submissions(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )

    resp = await client.get("/submissions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["file_object_key"]
    # Nested objects come back resolved.
    assert body[0]["student"]["name"] == "Ada Lovelace"
    assert body[0]["rubric"]["slug"] == "containers"
    assert body[0]["rubric"]["course_id"] == course_id
    assert body[0]["edition"]["name"] == "2026/27"
    assert body[0]["batch_id"] is None


async def test_filter_submissions_by_status(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )

    resp = await client.get("/submissions", params={"status": "graded"})
    assert resp.json() == []

    resp = await client.get("/submissions", params={"status": "pending"})
    assert len(resp.json()) == 1


async def test_filter_submissions_by_course_with_no_matching_rubrics(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    other_course = (await client.post("/courses", json={"name": "ML"})).json()

    resp = await client.get("/submissions", params={"course_id": other_course["id"]})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_filter_submissions_by_matching_ids(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()

    for params in (
        {"course_id": course_id},
        {"edition_id": edition_id},
        {"rubric_id": rubric_id},
        {"student_id": student_id},
        {"course_id": course_id, "edition_id": edition_id, "status": "pending"},
    ):
        resp = await client.get("/submissions", params=params)
        assert [s["id"] for s in resp.json()] == [created["id"]], params


async def test_filter_submissions_by_unrelated_edition_is_empty(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    other_edition = (await client.post("/editions", json={"name": "2027/28"})).json()

    resp = await client.get("/submissions", params={"edition_id": other_edition["id"]})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_submission_not_found(client):
    resp = await client.get("/submissions/999999999")
    assert resp.status_code == 404


async def test_download_submission_file_not_found(client):
    resp = await client.get("/submissions/999999999/file")
    assert resp.status_code == 404


async def test_download_submission_feedback_html_not_found(client):
    resp = await client.get("/submissions/999999999/feedback.html")
    assert resp.status_code == 404


async def test_download_submission_file_and_feedback(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"%PDF-data", "application/pdf")},
        )
    ).json()

    resp = await client.get(f"/submissions/{created['id']}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-data"
    assert resp.headers["content-type"] == "application/pdf"
    assert 'filename="s1_containers.pdf"' in resp.headers["content-disposition"]

    resp = await client.get(f"/submissions/{created['id']}/feedback.html")
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert "BDM" in resp.text  # the course name, looked up through the rubric
    assert 'filename="s1_containers_feedback.html"' in resp.headers["content-disposition"]


async def test_regrade_submission_resets_status_and_reenqueues(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()
    submission_id = created["id"]

    # Simulate a completed-but-failed run before forcing a re-grade.
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.FAILED
    submission.error = "Missing credentials"
    await save(client.db, submission)

    resp = await client.post(f"/submissions/{submission_id}/regrade")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["error"] is None

    # Enqueued once on create, once on regrade.
    assert client.delayed_submission_ids == [submission_id, submission_id]


async def test_bulk_regrade_submissions(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    submission_id = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()["id"]
    question_id = (await reload(client.db, Rubric, rubric_id)).questions[0].id

    resp = await client.post(
        "/submissions/regrade", json={"submission_ids": [submission_id], "question_id": question_id}
    )
    assert resp.status_code == 202
    assert resp.json() == {"regraded": 1}
    assert client.delayed_question_ids == [None, question_id]

    resp = await client.post("/submissions/regrade", json={"submission_ids": [submission_id]})
    assert resp.json() == {"regraded": 1}
    assert client.delayed_question_ids[-1] is None

    resp = await client.post("/submissions/regrade", json={"submission_ids": [submission_id], "question_id": "nope"})
    assert resp.status_code == 422


async def test_regrade_submission_not_found(client):
    resp = await client.post("/submissions/999999999/regrade")
    assert resp.status_code == 404


async def test_set_submission_student(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    student_id = await _make_student(client)

    # A batch-created submission starts with no student -- simulate one
    # directly rather than going through /batches (covered in
    # test_batches_api.py).
    rubric = await reload(client.db, Rubric, rubric_id)
    edition = await reload(client.db, Edition, edition_id)
    submission = Submission(rubric=rubric, edition=edition, student=None, file_object_key="k")
    await add(client.db, submission)

    resp = await client.put(f"/submissions/{submission.id}/student", json={"student_id": student_id})
    assert resp.status_code == 200
    assert resp.json()["student"]["id"] == student_id
    assert resp.json()["student"]["student_id"] == "s1"


async def test_set_submission_student_not_found(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    student_id = await _make_student(client)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()

    resp = await client.put(f"/submissions/{created['id']}/student", json={"student_id": "999999999"})
    assert resp.status_code == 404


async def test_set_submission_student_submission_not_found(client):
    student_id = await _make_student(client)
    resp = await client.put("/submissions/999999999/student", json={"student_id": student_id})
    assert resp.status_code == 404


async def _make_graded_submission(client) -> str:
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    submission_id = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()["id"]
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.GRADED
    submission.answers = [
        AnsweredQuestion(
            question_id="answer1", student_answer="It crashed.", grade=Grade(level="some_effort", feedback="LLM says")
        )
    ]
    await save(client.db, submission)
    return submission_id


async def test_set_manual_grade_keeps_llm_grade(client):
    submission_id = await _make_graded_submission(client)

    resp = await client.put(
        f"/submissions/{submission_id}/answers/answer1/grade", json={"level": "solid", "feedback": "TA says"}
    )
    assert resp.status_code == 200
    answer = resp.json()["answers"][0]
    assert answer["manual_grade"] == {"level": "solid", "feedback": "TA says"}
    assert answer["manual_graded_at"] is not None
    assert answer["grade"] == {"level": "some_effort", "feedback": "LLM says"}

    stored = (await reload(client.db, Submission, submission_id)).answers[0]
    assert stored.manual_grade == Grade(level="solid", feedback="TA says")
    assert stored.effective_grade == stored.manual_grade


async def test_clear_manual_grade_reverts_to_llm(client):
    submission_id = await _make_graded_submission(client)
    await client.put(f"/submissions/{submission_id}/answers/answer1/grade", json={"level": "solid", "feedback": "x"})

    resp = await client.delete(f"/submissions/{submission_id}/answers/answer1/grade")
    assert resp.status_code == 200
    answer = resp.json()["answers"][0]
    assert answer["manual_grade"] is None
    assert answer["manual_graded_at"] is None
    assert (await reload(client.db, Submission, submission_id)).answers[0].effective_grade.level == "some_effort"


# ---- general feedback: manual override + regenerate ----


async def test_set_and_clear_manual_general_feedback_keeps_llm_general_feedback(client):
    submission_id = await _make_graded_submission(client)
    submission = await reload(client.db, Submission, submission_id)
    submission.general_feedback = GeneralFeedback(feedback="LLM general feedback")
    await save(client.db, submission)

    resp = await client.put(
        f"/submissions/{submission_id}/general-feedback", json={"feedback": "  TA general feedback  "}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["manual_general_feedback"] == "TA general feedback"
    assert body["manual_general_feedback_at"] is not None
    assert body["general_feedback"]["feedback"] == "LLM general feedback"
    assert (await reload(client.db, Submission, submission_id)).effective_general_feedback == "TA general feedback"

    resp = await client.delete(f"/submissions/{submission_id}/general-feedback")
    assert resp.status_code == 200
    assert resp.json()["manual_general_feedback"] is None
    assert resp.json()["manual_general_feedback_at"] is None
    assert (await reload(client.db, Submission, submission_id)).effective_general_feedback == "LLM general feedback"


async def test_set_manual_general_feedback_blank_422(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.put(f"/submissions/{submission_id}/general-feedback", json={"feedback": "   "})
    assert resp.status_code == 422


async def test_set_manual_general_feedback_rejected_while_grading(client):
    submission_id = await _make_graded_submission(client)
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.GRADING
    await save(client.db, submission)
    resp = await client.put(f"/submissions/{submission_id}/general-feedback", json={"feedback": "x"})
    assert resp.status_code == 409


async def _with_general_feedback_instructions(client, submission_id: int) -> None:
    submission = await reload(client.db, Submission, submission_id)
    rubric = await reload(client.db, Rubric, submission.rubric_id)
    rubric.general_feedback_instructions = "One sentence."
    await save(client.db, rubric)


async def test_regenerate_general_feedback_enqueues_and_marks_grading(client):
    submission_id = await _make_graded_submission(client)
    await _with_general_feedback_instructions(client, submission_id)

    resp = await client.post(f"/submissions/{submission_id}/general-feedback/regenerate")
    assert resp.status_code == 202
    assert resp.json()["status"] == "grading"
    assert client.delayed_general_feedback == [(submission_id, client.default_llm_model_id)]
    # Only the general feedback: nothing re-graded.
    assert client.delayed_submission_ids == [submission_id]  # the upload itself


async def test_regenerate_general_feedback_without_instructions_422(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.post(f"/submissions/{submission_id}/general-feedback/regenerate")
    assert resp.status_code == 422
    assert client.delayed_general_feedback == []


async def test_regenerate_general_feedback_requires_graded_submission(client):
    submission_id = await _make_graded_submission(client)
    await _with_general_feedback_instructions(client, submission_id)
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.FAILED
    await save(client.db, submission)

    resp = await client.post(f"/submissions/{submission_id}/general-feedback/regenerate")
    assert resp.status_code == 409
    assert client.delayed_general_feedback == []


async def test_bulk_regenerate_general_feedback_skips_ungraded(client):
    graded_id = await _make_graded_submission(client)
    await _with_general_feedback_instructions(client, graded_id)
    graded = await reload(client.db, Submission, graded_id)
    failed = await add(
        client.db,
        Submission(
            rubric_id=graded.rubric_id,
            edition_id=graded.edition_id,
            file_object_key="k",
            status=SubmissionStatus.FAILED,
        ),
    )
    failed_id = failed.id

    resp = await client.post(
        "/submissions/general-feedback/regenerate", json={"submission_ids": [graded_id, failed_id]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"regenerated": 1, "skipped": 1}
    assert client.delayed_general_feedback == [(graded_id, client.default_llm_model_id)]
    assert (await reload(client.db, Submission, graded_id)).status == SubmissionStatus.GRADING
    assert (await reload(client.db, Submission, failed_id)).status == SubmissionStatus.FAILED


async def test_bulk_regenerate_general_feedback_without_instructions_422(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.post("/submissions/general-feedback/regenerate", json={"submission_ids": [submission_id]})
    assert resp.status_code == 422
    assert client.delayed_general_feedback == []


async def test_set_manual_grade_rejects_unknown_level(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/answers/answer1/grade", json={"level": "excellent", "feedback": "x"}
    )
    assert resp.status_code == 422


async def test_set_manual_grade_unknown_question(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/answers/nope/grade", json={"level": "solid", "feedback": "x"}
    )
    assert resp.status_code == 404


async def test_set_manual_grade_not_found(client):
    resp = await client.put("/submissions/999999999/answers/answer1/grade", json={"level": "solid", "feedback": "x"})
    assert resp.status_code == 404


async def test_set_manual_grade_rejected_while_grading(client):
    submission_id = await _make_graded_submission(client)
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.GRADING
    await save(client.db, submission)

    resp = await client.put(
        f"/submissions/{submission_id}/answers/answer1/grade", json={"level": "solid", "feedback": "x"}
    )
    assert resp.status_code == 409
    resp = await client.delete(f"/submissions/{submission_id}/answers/answer1/grade")
    assert resp.status_code == 409


async def test_answer_image_not_found(client):
    resp = await client.get("/submissions/999999999/answers/answer1/images/0")
    assert resp.status_code == 404


# --- Generic-document segmentation ------------------------------------------


async def _make_generic_submission(client, *, llm_segmentation: bool = True, converted: bool = True) -> str:
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)
    rubric = await reload(client.db, Rubric, rubric_id)
    rubric.questions = [_question(id="q1"), _question(id="q2")]
    await save(client.db, rubric)
    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id, "llm_segmentation": str(llm_segmentation).lower()},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 202, resp.text
    submission_id = resp.json()["id"]
    if converted:
        client.fake_objects["md-key"] = b"# Ex 1\nfoo\n# Ex 2\nbar"
        submission = await reload(client.db, Submission, submission_id)
        submission.document = ConvertedDocument(markdown_object_key="md-key", line_count=4, page_start_lines=[1])
        submission.status = SubmissionStatus.AWAITING_SEGMENTATION
        await save(client.db, submission)
    return submission_id


async def test_create_generic_pdf_submission_enqueues_conversion_not_grading(client):
    submission_id = await _make_generic_submission(client, converted=False)
    assert client.delayed_submission_ids == []
    assert client.delayed_prepares == [(submission_id, client.default_llm_model_id)]


async def test_create_generic_submission_converts_docx_to_pdf(client, minimal_docx):
    """A .docx against a generic-document rubric is converted to PDF at
    upload, so only a PDF is ever stored (and everything downstream is
    unchanged)."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.docx", minimal_docx("My answer"), "application/octet-stream")},
    )
    assert resp.status_code == 202, resp.text
    key = resp.json()["file_object_key"]
    assert key.endswith(".pdf")
    assert client.fake_objects[key].startswith(b"%PDF")
    assert client.delayed_prepares == [(resp.json()["id"], client.default_llm_model_id)]


async def test_converted_upload_keeps_original_for_provenance(client, minimal_docx):
    """The .docx itself is kept next to the converted PDF and downloadable,
    under its uploaded name (non-ASCII safe); deleting the submission
    removes it with the rest."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)
    docx = minimal_docx("My answer")

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("Resposta Àlex.docx", docx, "application/octet-stream")},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["original_filename"] == "Resposta Àlex.docx"
    original_key = body["original_object_key"]
    assert original_key == body["file_object_key"].removesuffix(".pdf") + "/original.docx"
    assert client.fake_objects[original_key] == docx

    resp = await client.get(f"/submissions/{body['id']}/original")
    assert resp.status_code == 200
    assert resp.content == docx
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Resposta _lex.docx"')
    assert "filename*=UTF-8''Resposta%20%C3%80lex.docx" in disposition

    assert (await client.delete(f"/submissions/{body['id']}?force=true")).status_code == 204
    assert original_key not in client.fake_objects


async def test_unconverted_upload_has_no_separate_original(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"%PDF-1.4 data", "application/pdf")},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["original_filename"] == "answers.pdf"
    assert resp.json()["original_object_key"] is None
    assert len(client.fake_objects) == 1
    assert (await client.get(f"/submissions/{resp.json()['id']}/original")).status_code == 404


async def test_create_generic_submission_rejects_unreadable_docx(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.docx", b"not a docx", "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert "answers.docx" in resp.json()["detail"]
    assert await client.db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_create_submission_rejects_docx_for_form_pdf_rubric(client, minimal_docx):
    """Only generic-document rubrics convert; a form-PDF rubric still needs a PDF."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)  # pdf

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.docx", minimal_docx("x"), "application/octet-stream")},
    )
    assert resp.status_code == 422


async def test_create_generic_pdf_submission_without_llm_segmentation(client):
    submission_id = await _make_generic_submission(client, llm_segmentation=False, converted=False)
    assert client.delayed_prepares == [(submission_id, None)]


async def test_get_submission_document(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.get(f"/submissions/{submission_id}/document")
    assert resp.status_code == 200
    assert resp.json() == {
        "markdown": "# Ex 1\nfoo\n# Ex 2\nbar",
        "line_count": 4,
        "page_start_lines": [1],
        "images": [],
    }


async def test_get_submission_document_not_converted_yet(client):
    submission_id = await _make_generic_submission(client, converted=False)
    assert (await client.get(f"/submissions/{submission_id}/document")).status_code == 409


async def test_set_segments_without_grading(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/segments", json={"segments": [{"question_id": "q1", "start_line": 1}]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_segmentation"
    assert body["segmentation_source"] == "manual"
    assert body["segments"] == [{"question_id": "q1", "start_line": 1, "end_line": None}]
    assert client.delayed_submission_ids == []


async def test_set_segments_and_grade(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/segments",
        json={
            "segments": [
                {"question_id": "q1", "start_line": 1, "end_line": 2},
                {"question_id": "q2", "start_line": None},
            ],
            "grade": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    assert client.delayed_submission_ids == [submission_id]


async def test_set_segments_grade_requires_every_question(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/segments",
        json={"segments": [{"question_id": "q1", "start_line": 1}], "grade": True},
    )
    assert resp.status_code == 422
    assert "q2" in resp.json()["detail"]


async def test_set_segments_rejects_invalid_lines(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.put(
        f"/submissions/{submission_id}/segments", json={"segments": [{"question_id": "q1", "start_line": 9}]}
    )
    assert resp.status_code == 422


async def test_set_segments_rejected_while_converting(client):
    submission_id = await _make_generic_submission(client)
    submission = await reload(client.db, Submission, submission_id)
    submission.status = SubmissionStatus.CONVERTING
    await save(client.db, submission)
    resp = await client.put(f"/submissions/{submission_id}/segments", json={"segments": []})
    assert resp.status_code == 409


async def test_set_segments_rejected_for_acroform_rubric(client):
    submission_id = await _make_graded_submission(client)
    resp = await client.put(f"/submissions/{submission_id}/segments", json={"segments": []})
    assert resp.status_code == 422


async def test_suggest_segments_enqueues(client):
    submission_id = await _make_generic_submission(client)
    resp = await client.post(f"/submissions/{submission_id}/segments/suggest")
    assert resp.status_code == 202
    assert resp.json()["status"] == "segmenting"
    assert client.delayed_suggestions == [(submission_id, client.default_llm_model_id)]


# ---- DELETE /submissions/{id} ----


async def _make_uploaded_submission(client) -> dict:
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    return (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()


async def test_delete_submission_removes_document_and_file(client):
    created = await _make_uploaded_submission(client)
    submission = await reload(client.db, Submission, created["id"])
    submission.status = SubmissionStatus.GRADED
    await save(client.db, submission)
    client.fake_objects[created["file_object_key"].rsplit(".", 1)[0] + "/document.md"] = b"# derived"

    resp = await client.delete(f"/submissions/{created['id']}")
    assert resp.status_code == 204
    assert await reload(client.db, Submission, created["id"]) is None
    assert client.fake_objects == {}


async def test_delete_submission_in_progress_needs_force(client):
    created = await _make_uploaded_submission(client)  # still `pending`

    resp = await client.delete(f"/submissions/{created['id']}")
    assert resp.status_code == 409
    assert await reload(client.db, Submission, created["id"]) is not None

    resp = await client.delete(f"/submissions/{created['id']}?force=true")
    assert resp.status_code == 204
    assert await reload(client.db, Submission, created["id"]) is None


async def test_delete_submission_not_found(client):
    resp = await client.delete("/submissions/999999999")
    assert resp.status_code == 404


# ---- general feedback CSV export ----


async def test_export_general_feedback_csv(client):
    db = client.db
    rubric = await make_rubric(db, "gf_csv")
    edition, other_edition = await make_edition(db, "gf_a"), await make_edition(db, "gf_b")
    zoe = await add(db, Student(student_id="s2", name="Zoe Ñúñez", username="zoe", email="zoe@x.edu"))
    ada = await add(db, Student(student_id="s1", name="Ada Lovelace"))
    await make_submission(
        db,
        rubric=rubric,
        edition=edition,
        student=zoe,
        general_feedback=GeneralFeedback(feedback="LLM text"),
        manual_general_feedback="Line one\nLine, two",
    )
    await make_submission(
        db, rubric=rubric, edition=edition, student=ada, general_feedback=GeneralFeedback(feedback="Good job")
    )
    await make_submission(db, rubric=rubric, edition=edition, label="Group 3", manual_general_feedback="Team text")
    await make_submission(db, rubric=rubric, edition=edition, student=ada)  # no general feedback: no row
    await make_submission(  # another edition: not exported
        db, rubric=rubric, edition=other_edition, student=ada, general_feedback=GeneralFeedback(feedback="Old")
    )

    resp = await client.get(
        "/submissions/general-feedback.csv", params={"rubric_id": rubric.id, "edition_id": edition.id}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "gf_csv_gf_a_general_feedback.csv" in resp.headers["content-disposition"]
    assert resp.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert rows == [
        ["ID number", "Full name", "Username", "Email address", "General feedback"],
        ["s1", "Ada Lovelace", "", "", "Good job"],
        ["", "Group 3", "", "", "Team text"],
        ["s2", "Zoe Ñúñez", "zoe", "zoe@x.edu", "Line one\nLine, two"],
    ]


async def test_export_general_feedback_csv_unknown_edition_404(client):
    rubric = await make_rubric(client.db, "gf_csv404")
    resp = await client.get(
        "/submissions/general-feedback.csv", params={"rubric_id": rubric.id, "edition_id": 999999999}
    )
    assert resp.status_code == 404
