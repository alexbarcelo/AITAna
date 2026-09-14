async def test_create_and_list_students(client):
    resp = await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["student_id"] == "s1"
    assert body["name"] == "Ada Lovelace"

    resp = await client.get("/students")
    assert resp.status_code == 200
    assert [s["student_id"] for s in resp.json()] == ["s1"]


async def test_create_duplicate_student_conflicts(client):
    await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})
    resp = await client.post("/students", json={"student_id": "s1", "name": "Someone Else"})
    assert resp.status_code == 409


async def test_get_student_not_found(client):
    resp = await client.get("/students/000000000000000000000000")
    assert resp.status_code == 404


async def test_get_student_by_id(client):
    created = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    resp = await client.get(f"/students/{created['_id']}")
    assert resp.status_code == 200
    assert resp.json()["student_id"] == "s1"


async def _make_edition(client, edition_name: str = "2026/27") -> str:
    edition = (await client.post("/editions", json={"name": edition_name})).json()
    return edition["_id"]


async def test_set_student_editions(client):
    student = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    edition1 = await _make_edition(client, edition_name="2026/27")
    edition2 = await _make_edition(client, edition_name="2027/28")

    resp = await client.put(f"/students/{student['_id']}/editions", json={"edition_ids": [edition1, edition2]})
    assert resp.status_code == 200
    assert sorted(resp.json()["edition_ids"]) == sorted([edition1, edition2])

    # Wholesale replace, not append.
    resp = await client.put(f"/students/{student['_id']}/editions", json={"edition_ids": [edition1]})
    assert resp.status_code == 200
    assert resp.json()["edition_ids"] == [edition1]


async def test_set_student_editions_unknown_edition_404(client):
    student = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    resp = await client.put(
        f"/students/{student['_id']}/editions", json={"edition_ids": ["000000000000000000000000"]}
    )
    assert resp.status_code == 404


async def test_set_student_editions_unknown_student_404(client):
    edition_id = await _make_edition(client)
    resp = await client.put(
        "/students/000000000000000000000000/editions", json={"edition_ids": [edition_id]}
    )
    assert resp.status_code == 404
