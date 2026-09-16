import csv
import io
from collections.abc import Callable
from enum import Enum

from beanie import PydanticObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ...documents import Edition, Student

router = APIRouter(prefix="/students", tags=["students"])


class StudentCreate(BaseModel):
    student_id: str
    name: str
    email: str | None = None


class StudentEditions(BaseModel):
    edition_ids: list[PydanticObjectId]


@router.get("", response_model=list[Student])
async def list_students() -> list[Student]:
    return await Student.find_all().to_list()


@router.post("", response_model=Student, status_code=201)
async def create_student(payload: StudentCreate) -> Student:
    if await Student.find_one(Student.student_id == payload.student_id):
        raise HTTPException(status_code=409, detail=f"Student {payload.student_id!r} already exists")
    student = Student(**payload.model_dump())
    await student.insert()
    return student


@router.get("/{student_id}", response_model=Student)
async def get_student(student_id: PydanticObjectId) -> Student:
    student = await Student.get(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.put("/{student_id}/editions", response_model=Student)
async def set_student_editions(student_id: PydanticObjectId, payload: StudentEditions) -> Student:
    """Replace a student's edition enrollments wholesale (a student can be in
    several editions -- e.g. retaking a course, or across different courses).
    Editions are global (see documents/edition.py), so this only records
    *when*, not *which course* -- there is no "set course" endpoint at all;
    course association lives on `Rubric`, not on the student/edition pair."""
    student = await Student.get(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    for edition_id in payload.edition_ids:
        if await Edition.get(edition_id) is None:
            raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    student.edition_ids = payload.edition_ids
    await student.save()
    return student


class StudentImportFormat(str, Enum):
    """Which roster-export shape an uploaded CSV follows. Only one today
    (Atenea, UPC's Moodle instance) -- add a new member plus a matching
    `_parse_*` function registered in `_IMPORT_PARSERS` for another, same
    shape as `SubmissionFormat`/`_EXTRACTORS` in grading/extraction/."""

    ATENEA = "atenea"


class _ImportedStudentRow(BaseModel):
    student_id: str
    name: str
    username: str | None = None
    email: str | None = None
    group: str | None = None


# Column names as they appear in an Atenea roster export, matched
# case-insensitively (see _parse_atenea_csv). "ID number" and "First name"
# are the only columns this project actually requires: "ID number" is the
# sole candidate for Student.student_id, the unique roster key everything
# else joins against, and "First name" is required so every imported
# student has at least a first name to be identified by (Student.name is
# required and this project doesn't fall back to some other column, e.g.
# Username, as a stand-in name). Every other column is optional and simply
# left unset when absent.
_ATENEA_ID_COLUMN = "id number"
_ATENEA_FIRST_NAME_COLUMN = "first name"
_ATENEA_LAST_NAME_COLUMN = "last name"
_ATENEA_USERNAME_COLUMN = "username"
_ATENEA_EMAIL_COLUMN = "email address"
_ATENEA_GROUP_COLUMN = "group"


def _parse_atenea_csv(raw: bytes) -> list[_ImportedStudentRow]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Could not decode the file as UTF-8: {exc}") from exc

    # Atenea exports have been seen both comma- and tab-separated depending
    # on the export path used -- sniff the header line rather than
    # hardcoding one delimiter, falling back to comma if sniffing itself is
    # inconclusive (e.g. a single-column file).
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV file has no header row")

    columns = {name.strip().lower(): name for name in reader.fieldnames if name}
    if _ATENEA_ID_COLUMN not in columns:
        raise ValueError('CSV is missing the required "ID number" column')
    if _ATENEA_FIRST_NAME_COLUMN not in columns:
        raise ValueError('CSV is missing the required "First name" column')

    def cell(row: dict[str, str], column: str) -> str:
        key = columns.get(column)
        value = row.get(key) if key else None
        return (value or "").strip()

    rows: list[_ImportedStudentRow] = []
    for line_number, row in enumerate(reader, start=2):  # header occupies line 1
        student_id = cell(row, _ATENEA_ID_COLUMN)
        if not student_id:
            raise ValueError(f'Row {line_number}: missing required "ID number" -- aborting import, nothing was saved')

        first_name = cell(row, _ATENEA_FIRST_NAME_COLUMN)
        if not first_name:
            raise ValueError(f'Row {line_number}: missing required "First name" -- aborting import, nothing was saved')
        last_name = cell(row, _ATENEA_LAST_NAME_COLUMN)
        name = f"{first_name} {last_name}" if last_name else first_name

        rows.append(
            _ImportedStudentRow(
                student_id=student_id,
                name=name,
                username=cell(row, _ATENEA_USERNAME_COLUMN) or None,
                email=cell(row, _ATENEA_EMAIL_COLUMN) or None,
                group=cell(row, _ATENEA_GROUP_COLUMN) or None,
            )
        )

    if not rows:
        raise ValueError("CSV file has no data rows")
    return rows


_IMPORT_PARSERS: dict[StudentImportFormat, Callable[[bytes], list[_ImportedStudentRow]]] = {
    StudentImportFormat.ATENEA: _parse_atenea_csv,
}


class StudentImportResult(BaseModel):
    created: int
    updated: int
    students: list[Student]


@router.post("/import", response_model=StudentImportResult)
async def import_students(
    file: UploadFile = File(...),
    format: StudentImportFormat = Form(...),
) -> StudentImportResult:
    """Bulk-create/update students from an uploaded roster CSV.

    All rows are parsed and validated before anything is written: a single
    bad row (missing ID number or First name, or an ID number reused by two
    rows in the same file) 422s the whole import instead of leaving a
    half-applied roster. An existing student (matched by `student_id`) has
    `name`/`username`/`email`/`group` overwritten wholesale with the row's
    values -- including clearing a field the row leaves blank -- rather than
    merged field by field.
    """
    parser = _IMPORT_PARSERS[format]
    try:
        rows = parser(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    seen: dict[str, int] = {}
    for line_number, row in enumerate(rows, start=2):
        if row.student_id in seen:
            raise HTTPException(
                status_code=422,
                detail=f'Duplicate "ID number" {row.student_id!r}: rows {seen[row.student_id]} and {line_number}',
            )
        seen[row.student_id] = line_number

    created = 0
    updated = 0
    students: list[Student] = []
    for row in rows:
        existing = await Student.find_one(Student.student_id == row.student_id)
        if existing is None:
            student = Student(
                student_id=row.student_id, name=row.name, username=row.username, email=row.email, group=row.group
            )
            await student.insert()
            created += 1
        else:
            existing.name = row.name
            existing.username = row.username
            existing.email = row.email
            existing.group = row.group
            await existing.save()
            student = existing
            updated += 1
        students.append(student)

    return StudentImportResult(created=created, updated=updated, students=students)
