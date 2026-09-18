from aitana.documents import Course, Edition, Rubric, Submission, SubmissionStatus
from aitana.grading.models import Question, SubmissionFormat


async def _make_student(client) -> str:
    resp = await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    return resp.json()["_id"]


async def _make_course(client, name: str = "BDM") -> str:
    return (await client.post("/courses", json={"name": name})).json()["_id"]


def _question(**overrides) -> Question:
    defaults = dict(id="answer1", title="Q1", question="What happened?", rubric="R")
    defaults.update(overrides)
    return Question(**defaults)


async def _make_rubric(
    course_id: str, edition_id: str | None = None, fmt: SubmissionFormat = SubmissionFormat.PDF
) -> str:
    course = await Course.get(course_id)
    edition = None
    if edition_id is not None:
        edition = await Edition.get(edition_id)
    rubric = Rubric(
        slug="containers", title="Containers lab", course=course, edition=edition, format=fmt, questions=[_question()]
    )
    await rubric.insert()
    return str(rubric.id)


async def test_create_submission_uploads_file_and_enqueues_grading(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"%PDF-1.4 fake content", "application/pdf")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["file_object_key"]

    assert client.delayed_submission_ids == [body["_id"]]
    assert client.fake_objects[body["file_object_key"]] == b"%PDF-1.4 fake content"


async def test_create_submission_uses_rubrics_edition_automatically(client):
    """Uploading against a rubric that has a fixed edition shouldn't need an
    explicit edition_id -- the rubric's own edition is used."""
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},  # no edition_id
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 202


async def test_create_submission_without_edition_requires_explicit_one(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    rubric_id = await _make_rubric(course_id)  # no fixed edition

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},  # no edition_id either
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 422


async def test_create_submission_explicit_edition_for_edition_agnostic_rubric(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id)  # no fixed edition

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id, "edition_id": edition_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 202


async def test_create_submission_unknown_student_404(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    resp = await client.post(
        "/submissions",
        data={"student_id": "000000000000000000000000", "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    assert resp.status_code == 404


async def test_create_submission_accepts_notebook_file_for_notebook_rubric(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id, fmt=SubmissionFormat.NOTEBOOK)

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
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)  # defaults to pdf

    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("submission.ipynb", b'{"cells": []}', "application/x-ipynb+json")},
    )
    assert resp.status_code == 422


async def test_list_submissions(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )

    # `fetch_links=True` resolves student/rubric/edition to full documents via
    # a MongoDB $lookup aggregation on the underlying DBRef -- mongomock's
    # aggregation emulation doesn't handle that, so under mongomock the
    # response falls back to unresolved link references. Full-document
    # resolution has been verified manually against a real mongod; here we
    # only assert what mongomock can actually exercise.
    resp = await client.get("/submissions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["file_object_key"]


async def test_filter_submissions_by_status(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )

    # `status` is a plain field, so this filter is exercised end-to-end here.
    # Filtering by student_id/rubric_id/edition_id goes through Beanie's Link
    # query, which compiles to a MongoDB DBRef dotted-path match -- real
    # MongoDB supports this natively (verified manually against a live
    # mongod), but mongomock's pure-Python query matcher does not
    # special-case DBRef fields for dotted access, so that path isn't
    # exercised here.
    resp = await client.get("/submissions", params={"status": "graded"})
    assert resp.json() == []

    resp = await client.get("/submissions", params={"status": "pending"})
    assert len(resp.json()) == 1


async def test_filter_submissions_by_course_with_no_matching_rubrics(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    other_course = (await client.post("/courses", json={"name": "ML"})).json()

    # No rubric belongs to this other course -- should safely come back
    # empty rather than error. See test_filter_submissions_by_status for why
    # a *matching* course_id/edition_id filter isn't exercised under
    # mongomock (same Link-query gap; verified separately against real
    # MongoDB, see AGENTS.md).
    resp = await client.get("/submissions", params={"course_id": other_course["_id"]})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_filter_submissions_by_unrelated_edition_is_empty(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": rubric_id},
        files={"file": ("answers.pdf", b"data", "application/pdf")},
    )
    other_edition = (await client.post("/editions", json={"name": "2027/28"})).json()

    resp = await client.get("/submissions", params={"edition_id": other_edition["_id"]})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_submission_not_found(client):
    resp = await client.get("/submissions/000000000000000000000000")
    assert resp.status_code == 404


async def test_download_submission_file_not_found(client):
    resp = await client.get("/submissions/000000000000000000000000/file")
    assert resp.status_code == 404


async def test_download_submission_feedback_html_not_found(client):
    # The happy path needs `submission.rubric`/`.student`/`.edition` actually
    # resolved (fetch_links=True) to render anything -- same
    # can't-exercise-it-under-mongomock gap as `/file` above (see AGENTS.md
    # sharp edge #5), verified instead via tests/test_feedback_export.py's
    # direct, already-resolved-in-memory unit tests.
    resp = await client.get("/submissions/000000000000000000000000/feedback.html")
    assert resp.status_code == 404


async def test_regrade_submission_resets_status_and_reenqueues(client):
    student_id = await _make_student(client)
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()
    submission_id = created["_id"]

    # Simulate a completed-but-failed run before forcing a re-grade.
    submission = await Submission.get(submission_id)
    submission.status = SubmissionStatus.FAILED
    submission.error = "Missing credentials"
    await submission.save()

    resp = await client.post(f"/submissions/{submission_id}/regrade")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["error"] is None

    # Enqueued once on create, once on regrade.
    assert client.delayed_submission_ids == [submission_id, submission_id]


async def test_regrade_submission_not_found(client):
    resp = await client.post("/submissions/000000000000000000000000/regrade")
    assert resp.status_code == 404


async def test_set_submission_student(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    student_id = await _make_student(client)

    # A batch-created submission starts with no student -- simulate one
    # directly rather than going through /batches (covered in
    # test_batches_api.py).
    rubric = await Rubric.get(rubric_id)
    edition = await Edition.get(edition_id)
    submission = Submission(rubric=rubric, edition=edition, student=None, file_object_key="k")
    await submission.insert()

    resp = await client.put(f"/submissions/{submission.id}/student", json={"student_id": student_id})
    assert resp.status_code == 200
    # Nested Link resolution keys the embedded document by `id`, not `_id`
    # -- compare on `student_id` instead, which is unambiguous either way.
    assert resp.json()["student"]["student_id"] == "s1"


async def test_set_submission_student_not_found(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    student_id = await _make_student(client)
    created = (
        await client.post(
            "/submissions",
            data={"student_id": student_id, "rubric_id": rubric_id},
            files={"file": ("answers.pdf", b"data", "application/pdf")},
        )
    ).json()

    resp = await client.put(
        f"/submissions/{created['_id']}/student", json={"student_id": "000000000000000000000000"}
    )
    assert resp.status_code == 404


async def test_set_submission_student_submission_not_found(client):
    student_id = await _make_student(client)
    resp = await client.put(
        "/submissions/000000000000000000000000/student", json={"student_id": student_id}
    )
    assert resp.status_code == 404
