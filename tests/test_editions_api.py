async def _make_course(client, name: str = "BDM") -> str:
    return (await client.post("/courses", json={"name": name})).json()["_id"]


async def test_create_and_list_editions(client):
    resp = await client.post("/editions", json={"name": "2026/27"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "2026/27"
    assert body["slug"] == "2026_27"

    resp = await client.get("/editions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_create_edition_explicit_slug(client):
    resp = await client.post("/editions", json={"name": "2026/27", "slug": "ay-26-27"})
    assert resp.status_code == 201
    assert resp.json()["slug"] == "ay-26-27"


async def test_create_edition_duplicate_slug_conflicts(client):
    await client.post("/editions", json={"name": "2026/27"})
    resp = await client.post("/editions", json={"name": "2026/27"})
    assert resp.status_code == 409


async def test_get_edition_not_found(client):
    resp = await client.get("/editions/000000000000000000000000")
    assert resp.status_code == 404


async def test_get_edition_by_id(client):
    created = (await client.post("/editions", json={"name": "2026/27"})).json()
    resp = await client.get(f"/editions/{created['_id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "2026/27"


async def test_edition_is_reusable_across_different_courses(client):
    """The whole point of editions being global: creating "2026/27" once and
    referencing it from rubrics of two unrelated courses should reuse the
    *same* Edition document, not require (or silently produce) a second,
    identically-named one."""
    bdm_id = await _make_course(client, "BDM")
    ml_id = await _make_course(client, "ML")
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]

    bdm_exam = await client.post(
        "/rubrics",
        json={
            "title": "BDM final",
            "course_id": bdm_id,
            "edition_id": edition_id,
            "questions": [{"id": "a1", "title": "Q1", "question": "Q?", "rubric": "R"}],
        },
    )
    ml_exam = await client.post(
        "/rubrics",
        json={
            "title": "ML final",
            "course_id": ml_id,
            "edition_id": edition_id,
            "questions": [{"id": "a1", "title": "Q1", "question": "Q?", "rubric": "R"}],
        },
    )
    assert bdm_exam.status_code == 201
    assert ml_exam.status_code == 201
    assert bdm_exam.json()["edition"]["slug"] == "2026_27"
    assert ml_exam.json()["edition"]["slug"] == "2026_27"

    # Still exactly one edition -- not two "2026/27"s.
    resp = await client.get("/editions")
    assert [e["_id"] for e in resp.json()] == [edition_id]
