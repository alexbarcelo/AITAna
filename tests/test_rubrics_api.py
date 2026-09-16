from aitana.documents import Course, Rubric
from aitana.grading.models import DEFAULT_GRADING_SCALE, Grade, Question, SubmissionFormat


async def _make_course(client, name: str = "BDM") -> str:
    return (await client.post("/courses", json={"name": name})).json()["_id"]


async def _make_rubric(course_id: str) -> Rubric:
    course = await Course.get(course_id)
    rubric = Rubric(
        slug="containers",
        title="Containers lab",
        course=course,
        questions=[
            Question(id="answer1", title="Q1", question="What happened?", rubric="Rubric", expected_points=["a point"])
        ],
    )
    await rubric.insert()
    return rubric


async def test_list_rubrics(client):
    course_id = await _make_course(client)
    await _make_rubric(course_id)
    resp = await client.get("/rubrics")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["slug"] == "containers"
    assert len(body[0]["questions"]) == 1
    assert body[0]["format"] == "pdf"  # default, unchanged for existing PDF-based rubrics
    assert body[0]["grading_scale"] == DEFAULT_GRADING_SCALE


async def test_get_rubric_not_found(client):
    resp = await client.get("/rubrics/000000000000000000000000")
    assert resp.status_code == 404


async def test_get_rubric_by_id(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.get(f"/rubrics/{rubric.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Containers lab"


def _question_payload(**overrides) -> dict:
    payload = {"id": "answer1", "title": "Q1", "question": "What happened?", "rubric": "Rubric text"}
    payload.update(overrides)
    return payload


async def test_create_rubric_from_form_derives_slug_from_title(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={"title": "Kafka & Airflow", "course_id": course_id, "questions": [_question_payload()]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "kafka_airflow"
    assert body["title"] == "Kafka & Airflow"
    assert len(body["questions"]) == 1
    assert body["course"]["name"] == "BDM"
    assert body["edition"] is None


async def test_create_rubric_from_form_with_explicit_slug(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={"title": "Spark", "slug": "spark-lab", "course_id": course_id, "questions": [_question_payload(id="a1")]},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "spark-lab"


async def test_create_rubric_requires_course(client):
    resp = await client.post(
        "/rubrics",
        json={"title": "Spark", "questions": [_question_payload(id="a1")]},
    )
    assert resp.status_code == 422  # course_id is a required field


async def test_create_rubric_unknown_course_404(client):
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Containers",
            "course_id": "000000000000000000000000",
            "questions": [_question_payload(id="a1")],
        },
    )
    assert resp.status_code == 404


async def test_create_rubric_duplicate_slug_conflicts(client):
    course_id = await _make_course(client)
    await _make_rubric(course_id)
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Containers again",
            "slug": "containers",
            "course_id": course_id,
            "questions": [_question_payload(id="a1")],
        },
    )
    assert resp.status_code == 409


async def test_create_rubric_with_edition(client):
    course_id = await _make_course(client)
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Final exam",
            "course_id": course_id,
            "edition_id": edition_id,
            "questions": [_question_payload(id="a1")],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["edition"]["name"] == "2026/27"


async def test_create_rubric_unknown_edition_404(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Final exam",
            "course_id": course_id,
            "edition_id": "000000000000000000000000",
            "questions": [_question_payload(id="a1")],
        },
    )
    assert resp.status_code == 404


async def test_create_rubric_with_notebook_format(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Pandas lab",
            "course_id": course_id,
            "format": "notebook",
            "questions": [_question_payload()],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["format"] == "notebook"


async def test_create_rubric_with_custom_grading_scale(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Pass/fail lab",
            "course_id": course_id,
            "grading_scale": {"fail": "Did not meet the bar.", "pass": "Met the bar."},
            "questions": [_question_payload()],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["grading_scale"] == {"fail": "Did not meet the bar.", "pass": "Met the bar."}


async def test_create_rubric_with_empty_grading_scale_422(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics",
        json={
            "title": "Broken",
            "course_id": course_id,
            "grading_scale": {},
            "questions": [_question_payload()],
        },
    )
    assert resp.status_code == 422


YAML_RUBRIC = b"""\
title: Containers lab
questions:
  - id: answer1
    title: Docker network isolation
    question: |
      Explain why containers on different networks can't resolve each other.
    rubric: |
      A good answer explains that DNS-based name resolution is scoped to a
      single Docker network.
    expected_points:
      - mentions DNS is scoped per network
  - id: answer2
    field: answer2b
    title: Bind mounts vs volumes
    question: When would you choose a bind mount over a named volume?
    rubric: Explain the tradeoff.
"""


async def test_upload_rubric_yaml(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "containers"
    assert body["title"] == "Containers lab"
    assert len(body["questions"]) == 2
    assert body["questions"][1]["field"] == "answer2b"
    assert body["questions"][0]["question"].startswith("Explain why")


async def test_upload_rubric_yaml_slug_override(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"slug": "custom-slug", "course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "custom-slug"


async def test_upload_rubric_yaml_duplicate_slug_conflicts(client):
    course_id = await _make_course(client)
    await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 409


async def test_upload_rubric_yaml_missing_questions_key(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("bad.yaml", b"not_questions: []", "application/x-yaml")},
    )
    assert resp.status_code == 422


async def test_upload_rubric_yaml_malformed(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("bad.yaml", b"questions: [this is not: valid: yaml", "application/x-yaml")},
    )
    assert resp.status_code == 422


async def test_upload_rubric_yaml_without_course_422(client):
    resp = await client.post(
        "/rubrics/upload",
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 422


async def test_upload_rubric_yaml_with_course_slug_key(client):
    await client.post("/courses", json={"name": "BDM"})  # slug: bdm
    yaml_with_course = YAML_RUBRIC + b"course_slug: bdm\n"
    resp = await client.post(
        "/rubrics/upload",
        files={"yaml_file": ("containers_questions.yaml", yaml_with_course, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["course"]["name"] == "BDM"


async def test_upload_rubric_yaml_unknown_course_slug_404(client):
    resp = await client.post(
        "/rubrics/upload",
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC + b"course_slug: nope\n", "application/x-yaml")},
    )
    assert resp.status_code == 404


async def test_upload_rubric_yaml_defaults_to_pdf_format(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["format"] == "pdf"


async def test_upload_rubric_yaml_with_format_form_field(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id, "format": "notebook"},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["format"] == "notebook"


async def test_upload_rubric_yaml_with_format_key(client):
    course_id = await _make_course(client)
    yaml_with_format = YAML_RUBRIC + b"format: notebook\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_format, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["format"] == "notebook"


async def test_upload_rubric_yaml_form_field_overrides_format_key(client):
    course_id = await _make_course(client)
    yaml_with_format = YAML_RUBRIC + b"format: notebook\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id, "format": "pdf"},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_format, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["format"] == "pdf"


async def test_upload_rubric_yaml_invalid_format_key_422(client):
    course_id = await _make_course(client)
    yaml_with_bad_format = YAML_RUBRIC + b"format: carrier_pigeon\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_bad_format, "application/x-yaml")},
    )
    assert resp.status_code == 422


async def test_upload_rubric_yaml_defaults_to_default_grading_scale(client):
    course_id = await _make_course(client)
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["grading_scale"] == DEFAULT_GRADING_SCALE


async def test_upload_rubric_yaml_with_grading_scale_key(client):
    course_id = await _make_course(client)
    yaml_with_scale = YAML_RUBRIC + b"grading_scale:\n  fail: Did not meet the bar.\n  pass: Met the bar.\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_scale, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["grading_scale"] == {"fail": "Did not meet the bar.", "pass": "Met the bar."}


async def test_upload_rubric_yaml_empty_grading_scale_key_422(client):
    course_id = await _make_course(client)
    yaml_with_empty_scale = YAML_RUBRIC + b"grading_scale: {}\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_empty_scale, "application/x-yaml")},
    )
    assert resp.status_code == 422


async def test_upload_rubric_yaml_unquoted_numeric_edition_slug_422(client):
    """A real YAML footgun: an unquoted `2026_27` parses as the integer
    202627 (YAML 1.1 treats `_` as a digit separator in numbers), not the
    string "2026_27" -- exactly the shape a global edition's slug takes.
    This must 422 with a clear message, not a baffling "not found" 404."""
    course_id = await _make_course(client)
    yaml_with_edition = YAML_RUBRIC + b"course_slug: bdm\nedition_slug: 2026_27\n"
    resp = await client.post(
        "/rubrics/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_edition, "application/x-yaml")},
    )
    assert resp.status_code == 422
    assert "quote" in resp.json()["detail"].lower()


async def test_upload_rubric_yaml_with_edition_slug_key(client):
    await _make_course(client)
    await client.post("/editions", json={"name": "2026/27"})  # slug: 2026_27
    # Quoted deliberately: an unquoted `2026_27` is parsed by YAML as the
    # integer 202627 (underscore-as-digit-separator), not a string -- see
    # _require_yaml_string's docstring in api/routers/rubrics.py.
    yaml_with_edition = YAML_RUBRIC + b'course_slug: bdm\nedition_slug: "2026_27"\n'
    resp = await client.post(
        "/rubrics/upload",
        files={"yaml_file": ("containers_questions.yaml", yaml_with_edition, "application/x-yaml")},
    )
    assert resp.status_code == 201
    assert resp.json()["edition"]["name"] == "2026/27"


# ---- POST /rubrics/{id}/test-answer ----
# Interactive "try this rubric" endpoint: grades one free-typed answer
# against a single question, synchronously, with no Submission/MinIO/Celery
# involved. Same fake `create_agent` shape as tests/test_grading.py -- the
# fake ignores the model/tools/response_format it's called with and just
# returns a preset Grade as the agent's structured_response.


class _FakeChatModel:
    """Stands in for get_chat_model()'s return value. grade_answer() always
    routes through create_agent (patched below) rather than calling any
    method on this directly, so it only needs to be a distinguishable
    placeholder object."""


def _patch_chat_model(monkeypatch, grade: Grade) -> None:
    monkeypatch.setattr("aitana.api.routers.rubrics.get_chat_model", lambda provider, model: _FakeChatModel())

    class _FakeCompiledAgent:
        def invoke(self, state):
            return {"structured_response": grade}

    def _fake_create_agent(*, model, tools, response_format):
        return _FakeCompiledAgent()

    monkeypatch.setattr("aitana.grading.grading.create_agent", _fake_create_agent)


async def test_test_rubric_answer_returns_grade(client, monkeypatch):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    _patch_chat_model(monkeypatch, Grade(level="solid", feedback="Great, covers everything."))

    resp = await client.post(
        f"/rubrics/{rubric.id}/test-answer",
        json={"question_id": "answer1", "answer": "Because of DNS scoping within a Docker network."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["level"] == "solid"
    assert body["feedback"] == "Great, covers everything."


async def test_test_rubric_answer_blank_answer_short_circuits_without_llm_call(client, monkeypatch):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    # If grade_answer actually called the LLM for a blank answer, this
    # fake would return "solid" -- asserting "not_attempted" proves the
    # short-circuit ran instead.
    _patch_chat_model(monkeypatch, Grade(level="solid", feedback="should never be returned"))

    resp = await client.post(
        f"/rubrics/{rubric.id}/test-answer",
        json={"question_id": "answer1", "answer": "   "},
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "not_attempted"


async def test_test_rubric_answer_rubric_not_found(client, monkeypatch):
    _patch_chat_model(monkeypatch, Grade(level="solid", feedback="x"))
    resp = await client.post(
        "/rubrics/000000000000000000000000/test-answer",
        json={"question_id": "answer1", "answer": "x"},
    )
    assert resp.status_code == 404


async def test_test_rubric_answer_unknown_question_id(client, monkeypatch):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    _patch_chat_model(monkeypatch, Grade(level="solid", feedback="x"))

    resp = await client.post(
        f"/rubrics/{rubric.id}/test-answer",
        json={"question_id": "does-not-exist", "answer": "x"},
    )
    assert resp.status_code == 404


# ---- PUT /rubrics/{id} (manual edit) ----


async def test_update_rubric_manual_edit(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)

    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={
            "title": "Containers lab v2",
            "slug": "containers",
            "course_id": course_id,
            "questions": [_question_payload(id="a1", title="New Q1")],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Containers lab v2"
    assert body["slug"] == "containers"
    assert len(body["questions"]) == 1
    assert body["questions"][0]["title"] == "New Q1"


async def test_update_rubric_not_found(client):
    course_id = await _make_course(client)
    resp = await client.put(
        "/rubrics/000000000000000000000000",
        json={"title": "X", "course_id": course_id, "questions": [_question_payload()]},
    )
    assert resp.status_code == 404


async def test_update_rubric_unknown_course_404(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={"title": "X", "course_id": "000000000000000000000000", "questions": [_question_payload()]},
    )
    assert resp.status_code == 404


async def test_update_rubric_unchanged_slug_is_not_a_conflict(client):
    """Saving a rubric back with its own current slug must not 409 against
    itself -- only creation, and edits that collide with a *different*
    rubric's slug, are conflicts."""
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={"title": "Containers lab renamed", "slug": "containers", "course_id": course_id, "questions": [_question_payload()]},
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "containers"


async def test_update_rubric_slug_conflicts_with_different_rubric(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    await client.post(
        "/rubrics",
        json={"title": "Spark", "slug": "spark-lab", "course_id": course_id, "questions": [_question_payload(id="a1")]},
    )
    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={"title": "Containers", "slug": "spark-lab", "course_id": course_id, "questions": [_question_payload()]},
    )
    assert resp.status_code == 409


async def test_update_rubric_with_empty_grading_scale_422(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={
            "title": "Containers",
            "course_id": course_id,
            "grading_scale": {},
            "questions": [_question_payload()],
        },
    )
    assert resp.status_code == 422


# No positive-match test for "format change is blocked once a submission
# exists": that requires `Submission.find(Submission.rubric.id == ...)` (the
# same query shape used by `_guard_format_change`) to actually match an
# existing row, which AGENTS.md's sharp edge #3 documents as untested (and,
# per manual verification while building this, actually returns zero rows)
# under mongomock -- a mongomock limitation in matching a DBRef subfield,
# not a Beanie or app bug. `_guard_format_change` itself is the same
# `Link.id ==` pattern already relied on (and only positive-verified against
# real MongoDB) by `list_submissions`.


async def test_update_rubric_format_change_allowed_without_submissions(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}",
        json={
            "title": "Containers",
            "course_id": course_id,
            "format": "notebook",
            "questions": [_question_payload()],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["format"] == "notebook"


# ---- PUT /rubrics/{id}/upload (upload updated version) ----


async def test_update_rubric_yaml_replaces_content(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)

    updated_yaml = b"""\
title: Containers lab (updated)
questions:
  - id: answer1
    title: Updated question
    question: What changed?
    rubric: Explain the change.
"""
    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("whatever_name.yaml", updated_yaml, "application/x-yaml")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Containers lab (updated)"
    assert body["slug"] == "containers"  # kept, not re-derived from the new filename
    assert len(body["questions"]) == 1
    assert body["questions"][0]["title"] == "Updated question"


async def test_update_rubric_yaml_not_found(client):
    course_id = await _make_course(client)
    resp = await client.put(
        "/rubrics/000000000000000000000000/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 404


async def test_update_rubric_yaml_with_slug_override(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        data={"slug": "containers-v2", "course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "containers-v2"


async def test_update_rubric_yaml_slug_conflicts_with_different_rubric(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    await client.post(
        "/rubrics",
        json={"title": "Spark", "slug": "spark-lab", "course_id": course_id, "questions": [_question_payload(id="a1")]},
    )
    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        data={"slug": "spark-lab", "course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 409


async def test_update_rubric_yaml_ignores_course_slug_key(client):
    """A rubric's course isn't editable via the "upload updated version"
    flow at all -- a `course_slug:` key in the re-uploaded file must be
    ignored, not silently move the rubric to a different course, even when
    that course exists. Only the `course_id` form field (which the frontend
    pins to the rubric's current course) can set it."""
    course_id = await _make_course(client, "BDM")
    other_course_id = await _make_course(client, "Other course")
    rubric = await _make_rubric(course_id)
    yaml_with_other_course = YAML_RUBRIC + b"course_slug: other_course\n"

    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_other_course, "application/x-yaml")},
    )
    assert resp.status_code == 200
    assert resp.json()["course"]["id"] == course_id
    assert resp.json()["course"]["id"] != other_course_id


async def test_update_rubric_yaml_ignores_edition_slug_key(client):
    """Same as course_slug above, but for edition: re-uploading a YAML file
    that happens to carry an `edition_slug:` key must not attach an edition
    to a rubric that doesn't have one."""
    course_id = await _make_course(client)
    await client.post("/editions", json={"name": "2026/27"})  # slug: 2026_27
    rubric = await _make_rubric(course_id)
    assert rubric.edition is None
    yaml_with_edition = YAML_RUBRIC + b'edition_slug: "2026_27"\n'

    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        data={"course_id": course_id},
        files={"yaml_file": ("containers_questions.yaml", yaml_with_edition, "application/x-yaml")},
    )
    assert resp.status_code == 200
    assert resp.json()["edition"] is None


async def test_update_rubric_yaml_without_course_id_422(client):
    """Since a `course_slug:` key can't set the course during an edit (see
    above), omitting `course_id` entirely on this endpoint must 422 -- not
    fall back to the YAML the way creation does."""
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.put(
        f"/rubrics/{rubric.id}/upload",
        files={"yaml_file": ("containers_questions.yaml", YAML_RUBRIC, "application/x-yaml")},
    )
    assert resp.status_code == 422


# ---- DELETE /rubrics/{id} ----


async def test_delete_rubric(client):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)
    resp = await client.delete(f"/rubrics/{rubric.id}")
    assert resp.status_code == 204

    resp = await client.get(f"/rubrics/{rubric.id}")
    assert resp.status_code == 404


async def test_delete_rubric_not_found(client):
    resp = await client.delete("/rubrics/000000000000000000000000")
    assert resp.status_code == 404


# No positive-match test for "delete is blocked once a submission/batch
# exists" -- same mongomock `Link.id ==` limitation as
# _guard_format_change's tests above (see that comment / AGENTS.md sharp
# edge #3). test_delete_rubric above already covers the "no
# submissions/batches -> deletes fine" path.


async def test_test_rubric_answer_llm_failure_returns_502(client, monkeypatch):
    course_id = await _make_course(client)
    rubric = await _make_rubric(course_id)

    def _broken_get_chat_model(provider, model):
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    monkeypatch.setattr("aitana.api.routers.rubrics.get_chat_model", _broken_get_chat_model)

    resp = await client.post(
        f"/rubrics/{rubric.id}/test-answer",
        json={"question_id": "answer1", "answer": "some answer"},
    )
    assert resp.status_code == 502
