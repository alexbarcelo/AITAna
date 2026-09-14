async def test_create_and_list_courses(client):
    resp = await client.post("/courses", json={"name": "BDM 2026/27"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "BDM 2026/27"
    assert body["slug"] == "bdm_2026_27"

    resp = await client.get("/courses")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_create_course_explicit_slug(client):
    resp = await client.post("/courses", json={"name": "BDM 2026/27", "slug": "bdm-26-27"})
    assert resp.status_code == 201
    assert resp.json()["slug"] == "bdm-26-27"


async def test_create_course_duplicate_slug_conflicts(client):
    await client.post("/courses", json={"name": "BDM 2026/27"})
    resp = await client.post("/courses", json={"name": "BDM again", "slug": "bdm_2026_27"})
    assert resp.status_code == 409


async def test_get_course_not_found(client):
    resp = await client.get("/courses/000000000000000000000000")
    assert resp.status_code == 404


async def test_get_course_by_id(client):
    created = (await client.post("/courses", json={"name": "BDM 2026/27"})).json()
    resp = await client.get(f"/courses/{created['_id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "BDM 2026/27"
