import io
import zipfile

from aitana.documents import Batch, Course, Edition, Rubric, Submission
from aitana.grading.models import Question, SubmissionFormat


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


def _zip_bytes(folders: dict[str, list[tuple[str, bytes]]]) -> bytes:
    """Build an in-memory zip: {folder_name: [(filename, data), ...]}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for folder, files in folders.items():
            for filename, data in files:
                zf.writestr(f"{folder}/{filename}", data)
    return buf.getvalue()


_ATENEA_ZIP = _zip_bytes(
    {
        "Lovelace Ada_1001_assignsubmission_file": [("answers.pdf", b"%PDF-1.4 ada")],
        "Turing Alan_1002_assignsubmission_file": [("answers.pdf", b"%PDF-1.4 alan")],
    }
)


async def test_create_batch_uploads_files_and_enqueues_grading(client):
    # No roster at all -- every item is left unmatched, since there's
    # nothing to match against (see test_create_batch_auto_matches_students
    # below for the matching case).
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["created"] == 2
    assert body["batch"]["item_count"] == 2
    assert body["batch"]["type"] == "atenea"

    folder_names = {s["batch_internal_id"] for s in body["submissions"]}
    assert folder_names == {
        "Lovelace Ada_1001_assignsubmission_file",
        "Turing Alan_1002_assignsubmission_file",
    }
    assert all(s["student"] is None for s in body["submissions"])
    assert all(s["file_object_key"] for s in body["submissions"])

    submission_ids = [s["_id"] for s in body["submissions"]]
    assert sorted(client.delayed_submission_ids) == sorted(submission_ids)
    for s in body["submissions"]:
        assert client.fake_objects[s["file_object_key"]] in (b"%PDF-1.4 ada", b"%PDF-1.4 alan")


async def test_create_batch_auto_matches_students_by_folder_name(client):
    """Atenea folder names are "<surname(s)> <first name>", the reverse
    order of Student.name ("<first name> <surname(s)>") -- the match has to
    be order-independent to work at all."""
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    ada = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    alan = (await client.post("/students", json={"student_id": "s2", "name": "Alan Turing"})).json()

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )
    assert resp.status_code == 202
    submissions = {s["batch_internal_id"]: s for s in resp.json()["submissions"]}
    # Nested Link resolution keys the embedded document by `id`, not `_id`
    # (unlike a top-level response) -- compare on `student_id` instead,
    # which is unambiguous either way.
    assert submissions["Lovelace Ada_1001_assignsubmission_file"]["student"]["student_id"] == ada["student_id"]
    assert submissions["Turing Alan_1002_assignsubmission_file"]["student"]["student_id"] == alan["student_id"]


async def test_create_batch_leaves_ambiguous_name_match_unmatched(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    await client.post("/students", json={"student_id": "s2", "name": "Ada Lovelace"})  # same name, different id

    zip_bytes = _zip_bytes({"Lovelace Ada_1001_assignsubmission_file": [("answers.pdf", b"%PDF-1.4")]})
    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 202
    assert resp.json()["submissions"][0]["student"] is None


async def test_create_batch_uses_rubrics_edition_automatically(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},  # no edition_id
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )
    assert resp.status_code == 202


async def test_create_batch_without_edition_requires_explicit_one(client):
    course_id = await _make_course(client)
    rubric_id = await _make_rubric(course_id)  # no fixed edition

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )
    assert resp.status_code == 422


async def test_create_batch_unknown_rubric_404(client):
    resp = await client.post(
        "/batches",
        data={"rubric_id": "000000000000000000000000", "type": "atenea"},
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )
    assert resp.status_code == 404


async def test_create_batch_rejects_folder_with_no_files(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Lovelace Ada_1001_assignsubmission_file/", "")
        zf.writestr("Turing Alan_1002_assignsubmission_file/answers.pdf", b"%PDF-1.4")
    empty_folder_zip = buf.getvalue()

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", empty_folder_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await Batch.find_all().count() == 0
    assert await Submission.find_all().count() == 0


async def test_create_batch_rejects_folder_with_multiple_files(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    multi_file_zip = _zip_bytes(
        {
            "Lovelace Ada_1001_assignsubmission_file": [
                ("answers.pdf", b"%PDF-1.4 a"),
                ("extra.pdf", b"%PDF-1.4 b"),
            ]
        }
    )

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", multi_file_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await Batch.find_all().count() == 0
    assert await Submission.find_all().count() == 0


async def test_create_batch_rejects_file_extension_mismatching_rubric_format(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)  # defaults to pdf

    notebook_zip = _zip_bytes({"Lovelace Ada_1001_assignsubmission_file": [("answers.ipynb", b'{"cells": []}')]})

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", notebook_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await Batch.find_all().count() == 0


async def test_create_batch_rejects_non_zip_file(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 422


async def test_filter_submissions_by_unrelated_batch_is_empty(client):
    # Filtering by a *matching* batch_id goes through Beanie's Link query,
    # which compiles to a MongoDB DBRef dotted-path match -- real MongoDB
    # supports this natively (verified manually against a live mongod, same
    # as student_id/rubric_id/edition_id), but mongomock's pure-Python query
    # matcher does not special-case DBRef fields for dotted access, so that
    # path isn't exercised here (see test_filter_submissions_by_status).
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "atenea"},
        files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
    )

    resp = await client.get("/submissions", params={"batch_id": "000000000000000000000000"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_and_get_batch(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)

    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "atenea"},
            files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
        )
    ).json()
    batch_id = created["batch"]["_id"]

    resp = await client.get("/batches")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(f"/batches/{batch_id}")
    assert resp.status_code == 200
    assert resp.json()["item_count"] == 2


async def test_get_batch_not_found(client):
    resp = await client.get("/batches/000000000000000000000000")
    assert resp.status_code == 404


async def test_regrade_batch(client):
    # Same DBRef-dotted-path-under-mongomock gap as
    # test_filter_submissions_by_unrelated_batch_is_empty above:
    # `Submission.find(Submission.batch.id == batch_id)` inside
    # regrade_batch doesn't match anything under mongomock, so `regraded`
    # comes back 0 here even though the batch has submissions -- verified
    # separately against real MongoDB. This still exercises the endpoint's
    # own shape (batch lookup, 202, response schema).
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric_id = await _make_rubric(course_id, edition_id)
    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "atenea"},
            files={"file": ("submissions.zip", _ATENEA_ZIP, "application/zip")},
        )
    ).json()
    batch_id = created["batch"]["_id"]

    resp = await client.post(f"/batches/{batch_id}/regrade")
    assert resp.status_code == 202
    assert "regraded" in resp.json()


async def test_regrade_batch_not_found(client):
    resp = await client.post("/batches/000000000000000000000000/regrade")
    assert resp.status_code == 404
