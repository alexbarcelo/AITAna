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


ATENEA_CSV = (
    b"First name,Last name,Username,ID number,Email address,Group\n"
    b"Ada,Lovelace,alovelace,s1,ada@example.com,Lab A\n"
    b"Alan,Turing,aturing,s2,alan@example.com,Lab B\n"
)


async def test_import_students_atenea_csv(client):
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", ATENEA_CSV, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2
    assert body["updated"] == 0

    resp = await client.get("/students")
    by_id = {s["student_id"]: s for s in resp.json()}
    assert by_id["s1"]["name"] == "Ada Lovelace"
    assert by_id["s1"]["username"] == "alovelace"
    assert by_id["s1"]["email"] == "ada@example.com"
    assert by_id["s1"]["group"] == "Lab A"
    assert by_id["s2"]["name"] == "Alan Turing"


async def test_import_students_tab_separated(client):
    tsv = (
        b"First name\tLast name\tUsername\tID number\tEmail address\tGroup\n"
        b"Ada\tLovelace\talovelace\ts1\tada@example.com\tLab A\n"
    )
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", tsv, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


async def test_import_students_first_name_only(client):
    csv_bytes = b"First name,ID number\nAda,s1\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    resp = await client.get("/students")
    assert resp.json()[0]["name"] == "Ada"


async def test_import_students_updates_existing_in_place(client):
    await client.post("/students", json={"student_id": "s1", "name": "Old Name", "email": "old@example.com"})

    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", ATENEA_CSV, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["updated"] == 1

    resp = await client.get("/students")
    by_id = {s["student_id"]: s for s in resp.json()}
    assert by_id["s1"]["name"] == "Ada Lovelace"
    assert by_id["s1"]["username"] == "alovelace"
    assert by_id["s1"]["email"] == "ada@example.com"


async def test_import_students_update_clears_fields_left_blank(client):
    await client.post(
        "/students",
        json={"student_id": "s1", "name": "Old Name", "email": "old@example.com"},
    )
    resp = (await client.get("/students")).json()[0]
    assert resp["email"] == "old@example.com"

    # Full overwrite, not a merge: a re-import with no Email/Group/Username
    # clears whatever was there before.
    csv_bytes = b"First name,ID number\nAda,s1\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200

    student = (await client.get("/students")).json()[0]
    assert student["name"] == "Ada"
    assert student["email"] is None
    assert student["username"] is None
    assert student["group"] is None


async def test_import_students_missing_id_number_column(client):
    csv_bytes = b"First name,Last name\nAda,Lovelace\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 422
    assert "ID number" in resp.json()["detail"]


async def test_import_students_missing_id_number_value_aborts_whole_import(client):
    csv_bytes = b"First name,ID number\nAda,s1\nAlan,\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 422

    # Nothing was written -- not even the valid row before the bad one.
    resp = await client.get("/students")
    assert resp.json() == []


async def test_import_students_missing_first_name_column(client):
    csv_bytes = b"Last name,ID number\nLovelace,s1\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 422
    assert "First name" in resp.json()["detail"]


async def test_import_students_missing_first_name_value_aborts_whole_import(client):
    csv_bytes = b"First name,ID number\nAda,s1\n,s2\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 422
    assert "First name" in resp.json()["detail"]

    # Nothing was written -- not even the valid row before the bad one.
    resp = await client.get("/students")
    assert resp.json() == []


async def test_import_students_duplicate_id_number_in_file(client):
    csv_bytes = b"First name,ID number\nAda,s1\nAda Clone,s1\n"
    resp = await client.post(
        "/students/import",
        data={"format": "atenea"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 422
    assert "Duplicate" in resp.json()["detail"]


async def test_import_students_unknown_format(client):
    resp = await client.post(
        "/students/import",
        data={"format": "not-a-real-format"},
        files={"file": ("roster.csv", ATENEA_CSV, "text/csv")},
    )
    assert resp.status_code == 422
