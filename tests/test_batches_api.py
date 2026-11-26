import io
import zipfile

from sqlalchemy import func, select

from aitana.db import Batch, Course, Edition, GradingTrace, Rubric, Submission, SubmissionStatus
from aitana.grading.models import Grade, GradeTrace, Question, SubmissionFormat

from .factories import add, reload


async def _count(client, model) -> int:
    return await client.db.scalar(select(func.count()).select_from(model))


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


def _zip_bytes(folders: dict[str, list[tuple[str, bytes]]]) -> bytes:
    """Build an in-memory zip: {folder_name: [(filename, data), ...]}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for folder, files in folders.items():
            for filename, data in files:
                zf.writestr(f"{folder}/{filename}", data)
    return buf.getvalue()


_MOODLE_ZIP = _zip_bytes(
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
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["created"] == 2
    assert body["batch"]["item_count"] == 2
    assert body["batch"]["type"] == "moodle"

    folder_names = {s["batch_internal_id"] for s in body["submissions"]}
    assert folder_names == {
        "Lovelace Ada_1001_assignsubmission_file",
        "Turing Alan_1002_assignsubmission_file",
    }
    assert all(s["student"] is None for s in body["submissions"])
    assert all(s["file_object_key"] for s in body["submissions"])

    submission_ids = [s["id"] for s in body["submissions"]]
    assert sorted(client.delayed_submission_ids) == sorted(submission_ids)
    for s in body["submissions"]:
        assert client.fake_objects[s["file_object_key"]] in (b"%PDF-1.4 ada", b"%PDF-1.4 alan")


async def test_create_batch_auto_matches_students_by_folder_name(client):
    """Moodle folder names are "<surname(s)> <first name>", the reverse
    order of Student.name ("<first name> <surname(s)>") -- the match has to
    be order-independent to work at all."""
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    ada = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    alan = (await client.post("/students", json={"student_id": "s2", "name": "Alan Turing"})).json()

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 202
    submissions = {s["batch_internal_id"]: s for s in resp.json()["submissions"]}
    assert submissions["Lovelace Ada_1001_assignsubmission_file"]["student"]["id"] == ada["id"]
    assert submissions["Turing Alan_1002_assignsubmission_file"]["student"]["id"] == alan["id"]


async def test_create_batch_without_student_matching_is_labelled(client):
    """match_students=false (group projects): no auto-match even when a
    roster student's name fits, and each item is labelled by its folder
    name minus Moodle's suffix."""
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle", "match_students": "false"},
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 202
    submissions = {s["batch_internal_id"]: s for s in resp.json()["submissions"]}
    ada = submissions["Lovelace Ada_1001_assignsubmission_file"]
    assert ada["student"] is None
    assert ada["label"] == "Lovelace Ada"
    assert submissions["Turing Alan_1002_assignsubmission_file"]["label"] == "Turing Alan"


async def test_create_batch_leaves_ambiguous_name_match_unmatched(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    await client.post("/students", json={"student_id": "s2", "name": "Ada Lovelace"})  # same name, different id

    zip_bytes = _zip_bytes({"Lovelace Ada_1001_assignsubmission_file": [("answers.pdf", b"%PDF-1.4")]})
    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 202
    assert resp.json()["submissions"][0]["student"] is None


async def test_create_batch_uses_rubrics_edition_automatically(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},  # no edition_id
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 202


async def test_create_batch_without_edition_requires_explicit_one(client):
    course_id = await _make_course(client)
    rubric_id = await _make_rubric(client, course_id)  # no fixed edition

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 422


async def test_create_batch_unknown_rubric_404(client):
    resp = await client.post(
        "/batches",
        data={"rubric_id": "999999999", "type": "moodle"},
        files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
    )
    assert resp.status_code == 404


async def test_create_batch_rejects_folder_with_no_files(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Lovelace Ada_1001_assignsubmission_file/", "")
        zf.writestr("Turing Alan_1002_assignsubmission_file/answers.pdf", b"%PDF-1.4")
    empty_folder_zip = buf.getvalue()

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", empty_folder_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await _count(client, Batch) == 0
    assert await _count(client, Submission) == 0


async def test_create_batch_rejects_folder_with_multiple_files(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

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
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", multi_file_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await _count(client, Batch) == 0
    assert await _count(client, Submission) == 0


async def test_create_batch_rejects_file_extension_mismatching_rubric_format(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)  # defaults to pdf

    notebook_zip = _zip_bytes({"Lovelace Ada_1001_assignsubmission_file": [("answers.ipynb", b'{"cells": []}')]})

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", notebook_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert await _count(client, Batch) == 0


async def test_create_generic_batch_converts_docx_items(client, minimal_docx):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)

    mixed_zip = _zip_bytes(
        {
            "Lovelace Ada_1001_assignsubmission_file": [("answers.docx", minimal_docx("Ada"))],
            "Turing Alan_1002_assignsubmission_file": [("answers.pdf", b"%PDF-1.4 alan")],
        }
    )
    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", mixed_zip, "application/zip")},
    )
    assert resp.status_code == 202, resp.text
    stored = [client.fake_objects[s["file_object_key"]] for s in resp.json()["submissions"]]
    assert all(s["file_object_key"].endswith(".pdf") for s in resp.json()["submissions"])
    assert all(data.startswith(b"%PDF") for data in stored)
    assert b"%PDF-1.4 alan" in stored  # PDFs stored untouched

    # Provenance: every item records its in-zip filename; only the converted
    # .docx keeps a separate original, byte-for-byte.
    by_folder = {s["batch_internal_id"]: s for s in resp.json()["submissions"]}
    ada = by_folder["Lovelace Ada_1001_assignsubmission_file"]
    alan = by_folder["Turing Alan_1002_assignsubmission_file"]
    assert ada["original_filename"] == "answers.docx"
    assert alan["original_filename"] == "answers.pdf"
    assert alan["original_object_key"] is None
    assert ada["original_object_key"].endswith("/original.docx")
    assert client.fake_objects[ada["original_object_key"]] == minimal_docx("Ada")


async def test_create_generic_batch_unreadable_docx_writes_nothing(client, minimal_docx):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id, fmt=SubmissionFormat.GENERIC_DOCUMENT)

    bad_zip = _zip_bytes(
        {
            "Lovelace Ada_1001_assignsubmission_file": [("answers.docx", minimal_docx("Ada"))],
            "Turing Alan_1002_assignsubmission_file": [("answers.docx", b"corrupt")],
        }
    )
    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", bad_zip, "application/zip")},
    )
    assert resp.status_code == 422
    assert "Turing Alan_1002_assignsubmission_file/answers.docx" in resp.json()["detail"]
    assert await _count(client, Batch) == 0
    assert await _count(client, Submission) == 0


async def test_create_batch_rejects_non_zip_file(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    resp = await client.post(
        "/batches",
        data={"rubric_id": rubric_id, "type": "moodle"},
        files={"file": ("submissions.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 422


async def test_filter_submissions_by_batch(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()

    resp = await client.get("/submissions", params={"batch_id": created["batch"]["id"]})
    assert sorted(s["id"] for s in resp.json()) == sorted(s["id"] for s in created["submissions"])
    assert all(s["batch_id"] == created["batch"]["id"] for s in resp.json())

    resp = await client.get("/submissions", params={"batch_id": "999999999"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_and_get_batch(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)

    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()
    batch_id = created["batch"]["id"]

    resp = await client.get("/batches")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(f"/batches/{batch_id}")
    assert resp.status_code == 200
    assert resp.json()["item_count"] == 2
    assert resp.json()["rubric"]["slug"] == "containers"
    assert resp.json()["edition"]["name"] == "2026/27"

    resp = await client.get("/batches", params={"rubric_id": rubric_id})
    assert [b["id"] for b in resp.json()] == [batch_id]
    resp = await client.get("/batches", params={"rubric_id": 999999999})
    assert resp.json() == []


async def test_get_batch_not_found(client):
    resp = await client.get("/batches/999999999")
    assert resp.status_code == 404


async def test_download_batch_feedback_zip_not_found(client):
    resp = await client.get("/batches/999999999/feedback.zip")
    assert resp.status_code == 404


async def test_download_batch_feedback_zip(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()

    resp = await client.get(f"/batches/{created['batch']['id']}/feedback.zip")
    assert resp.status_code == 200
    assert 'filename="containers_feedback.zip"' in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = sorted(zf.namelist())
        assert names == [
            "Lovelace Ada_1001_assignsubmission_file/feedback.html",
            "Turing Alan_1002_assignsubmission_file/feedback.html",
        ]
        assert "BDM" in zf.read(names[0]).decode()


async def test_regrade_batch(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()
    batch_id = created["batch"]["id"]

    client.delayed_submission_ids.clear()
    resp = await client.post(f"/batches/{batch_id}/regrade")
    assert resp.status_code == 202
    assert resp.json() == {"regraded": 2}
    assert sorted(client.delayed_submission_ids) == sorted(s["id"] for s in created["submissions"])
    for s in created["submissions"]:
        assert (await reload(client.db, Submission, s["id"])).status == SubmissionStatus.PENDING


async def test_regrade_batch_single_question(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    created = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()
    batch_id = created["batch"]["id"]
    question_id = _question().id

    resp = await client.post(f"/batches/{batch_id}/regrade", params={"question_id": question_id})
    assert resp.status_code == 202

    resp = await client.post(f"/batches/{batch_id}/regrade", params={"question_id": "nope"})
    assert resp.status_code == 422


async def test_regrade_batch_not_found(client):
    resp = await client.post("/batches/999999999/regrade")
    assert resp.status_code == 404


# ---- DELETE /batches/{id} ----


async def test_delete_batch(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["id"]
    rubric_id = await _make_rubric(client, course_id, edition_id)
    body = (
        await client.post(
            "/batches",
            data={"rubric_id": rubric_id, "type": "moodle"},
            files={"file": ("submissions.zip", _MOODLE_ZIP, "application/zip")},
        )
    ).json()
    batch_id = body["batch"]["id"]

    # Freshly uploaded = still pending: refused without force.
    resp = await client.delete(f"/batches/{batch_id}")
    assert resp.status_code == 409
    assert await _count(client, Submission) == 2

    first = await reload(client.db, Submission, body["submissions"][0]["id"])
    client.db.add(
        GradingTrace(
            submission_id=first.id,
            question_id="answer1",
            trace=GradeTrace(
                provider="openai",
                model="m",
                system_prompt="p",
                student_answer="a",
                grade=Grade(level="solid", feedback="f"),
            ),
        )
    )
    await client.db.commit()

    resp = await client.delete(f"/batches/{batch_id}", params={"force": "true"})
    assert resp.status_code == 204
    assert (await client.get(f"/batches/{batch_id}")).status_code == 404
    assert await _count(client, Submission) == 0
    assert await _count(client, GradingTrace) == 0
    assert client.fake_objects == {}


async def test_delete_batch_not_found(client):
    resp = await client.delete("/batches/999999999")
    assert resp.status_code == 404
