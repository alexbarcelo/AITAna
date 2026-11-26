import io
import zipfile


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
    resp = await client.get("/students/999999999")
    assert resp.status_code == 404


async def test_get_student_by_id(client):
    created = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    resp = await client.get(f"/students/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["student_id"] == "s1"


# Includes a "Group" column on purpose: Student has no `group` field, so
# this doubles as a check that the column is silently ignored, not rejected.
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

    # Full overwrite, not a merge: a re-import with no Email/Username
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


LEARNSQL_HEADER = (
    "Nom",
    "Cognoms",
    "Número ID",
    "Institució",
    "Departament",
    "Adreça electrònica",
    "Suspès",
    "Darrera descàrrega des d'aquest curs",
)


def _learnsql_ods(rows_xml: str) -> bytes:
    """A minimal .ods: header row from LEARNSQL_HEADER, then `rows_xml`, then
    the huge repeated blank rows/cells LibreOffice writes after the data."""
    header = "".join(
        f'<table:table-cell office:value-type="string"><text:p>{name}</text:p></table:table-cell>'
        for name in LEARNSQL_HEADER
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<office:body><office:spreadsheet><table:table table:name="Students">'
        '<table:table-column table:number-columns-repeated="8"/>'
        f"<table:table-row>{header}"
        '<table:table-cell table:number-columns-repeated="16376"/></table:table-row>'
        f"{rows_xml}"
        '<table:table-row table:number-rows-repeated="1048570">'
        '<table:table-cell table:number-columns-repeated="16384"/></table:table-row>'
        "</table:table></office:spreadsheet></office:body></office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)
    return buf.getvalue()


def _ods_text_cell(text: str) -> str:
    return f'<table:table-cell office:value-type="string"><text:p>{text}</text:p></table:table-cell>'


async def test_import_students_learnsql_csv(client):
    csv_bytes = (
        ";".join(LEARNSQL_HEADER)
        + "\nAda;Lovelace Byron;s1;UPC;FIB;ada@example.com;No;Mai\n"
        + "Alan;;s2;UPC;FIB;;No;Mai\n"
    ).encode()
    resp = await client.post(
        "/students/import",
        data={"format": "learnsql"},
        files={"file": ("roster.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 2

    by_id = {s["student_id"]: s for s in (await client.get("/students")).json()}
    assert by_id["s1"]["name"] == "Ada Lovelace Byron"
    assert by_id["s1"]["email"] == "ada@example.com"
    assert by_id["s1"]["username"] is None
    assert by_id["s2"]["name"] == "Alan"
    assert by_id["s2"]["email"] is None


async def test_import_students_learnsql_ods(client):
    rows = (
        # Two identical adjacent cells (Institució, Departament) stored as one
        # repeated cell; the email as a hyperlink; a numeric ID cell whose
        # displayed text has a thousands separator.
        "<table:table-row>"
        + _ods_text_cell("Ada")
        + _ods_text_cell("Lovelace<text:s/>Byron")
        + '<table:table-cell office:value-type="float" office:value="12345"><text:p>12.345</text:p></table:table-cell>'
        + '<table:table-cell table:number-columns-repeated="2" office:value-type="string"><text:p>UPC</text:p>'
        "</table:table-cell>" + '<table:table-cell office:value-type="string"><text:p>'
        '<text:a xlink:href="mailto:ada@example.com">ada@example.com</text:a></text:p></table:table-cell>'
        + _ods_text_cell("No")
        + '<table:table-cell table:number-columns-repeated="16377"/>'
        "</table:table-row>"
        # Missing Cognoms and email: blank cells in the middle.
        "<table:table-row>"
        + _ods_text_cell("Alan")
        + "<table:table-cell/>"
        + _ods_text_cell("s2")
        + '<table:table-cell table:number-columns-repeated="16381"/>'
        "</table:table-row>"
    )
    resp = await client.post(
        "/students/import",
        data={"format": "learnsql"},
        files={"file": ("roster.ods", _learnsql_ods(rows), "application/vnd.oasis.opendocument.spreadsheet")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 2

    by_id = {s["student_id"]: s for s in (await client.get("/students")).json()}
    assert by_id["12345"]["name"] == "Ada Lovelace Byron"
    assert by_id["12345"]["email"] == "ada@example.com"
    assert by_id["s2"]["name"] == "Alan"
    assert by_id["s2"]["email"] is None


async def test_import_students_learnsql_missing_id_value_names_the_row(client):
    rows = "<table:table-row>" + _ods_text_cell("Ada") + _ods_text_cell("Lovelace") + "</table:table-row>"
    resp = await client.post(
        "/students/import",
        data={"format": "learnsql"},
        files={"file": ("roster.ods", _learnsql_ods(rows), "application/vnd.oasis.opendocument.spreadsheet")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith('Row 2: missing required "Número ID"')
    assert (await client.get("/students")).json() == []


async def test_import_students_learnsql_rejects_atenea_file(client):
    resp = await client.post(
        "/students/import",
        data={"format": "learnsql"},
        files={"file": ("roster.csv", ATENEA_CSV, "text/csv")},
    )
    assert resp.status_code == 422
    assert "Número ID" in resp.json()["detail"]


async def test_import_students_learnsql_unreadable_ods(client):
    resp = await client.post(
        "/students/import",
        data={"format": "learnsql"},
        files={"file": ("roster.ods", b"PK\x03\x04not really a zip", "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert "ODS" in resp.json()["detail"]


async def test_delete_student(client):
    created = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    resp = await client.delete(f"/students/{created['id']}")
    assert resp.status_code == 204
    assert (await client.get(f"/students/{created['id']}")).status_code == 404


async def test_delete_student_not_found(client):
    resp = await client.delete("/students/999999999")
    assert resp.status_code == 404


async def test_delete_student_blocked_by_submissions(client):
    from aitana.db import Student

    from .factories import make_submission

    created = (await client.post("/students", json={"student_id": "s1", "name": "Ada Lovelace"})).json()
    await make_submission(client.db, student=await client.db.get(Student, created["id"]))
    resp = await client.delete(f"/students/{created['id']}")
    assert resp.status_code == 409
    assert "1 submission(s)" in resp.json()["detail"]
