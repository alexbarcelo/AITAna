import csv
import io
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Student, Submission
from ...db.session import get_session
from ..schemas import StudentOut

router = APIRouter(prefix="/students", tags=["students"])


class StudentCreate(BaseModel):
    student_id: str
    name: str
    email: str | None = None


@router.get("", response_model=list[StudentOut])
async def list_students(session: AsyncSession = Depends(get_session)) -> list[Student]:
    return list(await session.scalars(select(Student).order_by(Student.id)))


@router.post("", response_model=StudentOut, status_code=201)
async def create_student(payload: StudentCreate, session: AsyncSession = Depends(get_session)) -> Student:
    if await session.scalar(select(Student).where(Student.student_id == payload.student_id)):
        raise HTTPException(status_code=409, detail=f"Student {payload.student_id!r} already exists")
    student = Student(**payload.model_dump())
    session.add(student)
    await session.commit()
    return student


@router.get("/{student_id}", response_model=StudentOut)
async def get_student(student_id: int, session: AsyncSession = Depends(get_session)) -> Student:
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.delete("/{student_id}", status_code=204)
async def delete_student(student_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """`409` if any submission is linked to this student. The foreign key
    would refuse the delete anyway; counting first gives a message that says
    why."""
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    linked = await session.scalar(select(func.count()).where(Submission.student_id == student.id))
    if linked:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete {student.name}: {linked} submission(s) are linked to this student.",
        )
    await session.delete(student)
    await session.commit()


class StudentImportFormat(str, Enum):
    """Which roster-export shape an uploaded file follows -- add a new member
    plus a matching parser registered in `_IMPORT_PARSERS` for another, same
    shape as `SubmissionFormat`/`_EXTRACTORS` in grading/extraction/.

    - `atenea`: Atenea (UPC's Moodle instance) roster export, CSV.
    - `learnsql`: LearnSQL's student list, as the `.ods` it exports or that
      sheet saved as CSV."""

    ATENEA = "atenea"
    LEARNSQL = "learnsql"


class _ImportedStudentRow(BaseModel):
    row_number: int = Field(exclude=True)  # in the file, for error messages
    student_id: str
    name: str
    username: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class _RosterColumns:
    """Header names of one roster format, as they appear in the file (used
    verbatim in error messages; matched case-insensitively). `student_id`
    and `first_name` are the only required ones: the id is the sole source
    of `Student.student_id`, the unique roster key everything else joins
    against, and the first name is required so every imported student has a
    real name to be identified by (`Student.name` is required and this
    project doesn't fall back to some other column as a stand-in). The rest
    are optional, left unset when absent; any other column is ignored."""

    student_id: str
    first_name: str
    last_name: str
    email: str
    username: str | None = None


# Atenea also exports a "Group" column, ignored: there is no `Student.group`.
_ATENEA_COLUMNS = _RosterColumns(
    student_id="ID number",
    first_name="First name",
    last_name="Last name",
    email="Email address",
    username="Username",
)
# LearnSQL's full header is: Nom, Cognoms, Número ID, Institució,
# Departament, Adreça electrònica, Suspès, Darrera descàrrega des d'aquest
# curs. It has no username.
_LEARNSQL_COLUMNS = _RosterColumns(
    student_id="Número ID",
    first_name="Nom",
    last_name="Cognoms",
    email="Adreça electrònica",
)

# A table is a list of rows (the header first), each `(row number in the
# file, cells)`; fully blank rows are already dropped.
_Table = list[tuple[int, list[str]]]


def _header_key(name: str) -> str:
    # NFC: an accented header ("Número ID") may come decomposed from some tools.
    return unicodedata.normalize("NFC", name).strip().casefold()


def _read_csv(raw: bytes) -> _Table:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Could not decode the file as UTF-8: {exc}") from exc

    # Exports have been seen comma-, semicolon- and tab-separated depending on
    # the tool/locale -- sniff the header line rather than hardcoding one
    # delimiter, falling back to comma if sniffing itself is inconclusive
    # (e.g. a single-column file).
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    return [
        (line_number, row)
        for line_number, row in enumerate(csv.reader(io.StringIO(text), dialect=dialect), start=1)
        if any(cell.strip() for cell in row)
    ]


_ODS_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODS_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODS_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
# content.xml of a roster is a few hundred KB at most; refuse to inflate
# anything absurd (zip bomb).
_ODS_MAX_CONTENT_BYTES = 50 * 1024 * 1024


def _ods_text(element: ET.Element) -> str:
    """Text of a `text:p`, expanding ODF's whitespace elements (`text:s` is
    a run of `text:c` spaces) and descending into spans/links."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == f"{_ODS_TEXT}s":
            parts.append(" " * int(child.get(f"{_ODS_TEXT}c", "1")))
        elif child.tag == f"{_ODS_TEXT}tab":
            parts.append("\t")
        elif child.tag == f"{_ODS_TEXT}line-break":
            parts.append("\n")
        else:
            parts.append(_ods_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _ods_cell_value(cell: ET.Element) -> str:
    # A numeric cell (an ID number typed as a number) is read from its raw
    # value, not its displayed text, which may carry thousands separators.
    if cell.get(f"{_ODS_OFFICE}value-type") == "float" and cell.get(f"{_ODS_OFFICE}value") is not None:
        value = Decimal(cell.get(f"{_ODS_OFFICE}value"))
        return str(value.quantize(Decimal(1))) if value == value.to_integral_value() else str(value)
    return "\n".join(_ods_text(p) for p in cell.iter(f"{_ODS_TEXT}p"))


def _read_ods(raw: bytes) -> _Table:
    """First sheet of an OpenDocument spreadsheet, read with the standard
    library only (an `.ods` is a zip holding `content.xml`): no odfpy/pandas
    dependency for what is a flat list of text cells. Repeated cells/rows
    (`number-columns-repeated`/`number-rows-repeated`, how ODF stores runs of
    identical cells -- including the thousands of empty ones after the data)
    are expanded, except for trailing blanks."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if archive.getinfo("content.xml").file_size > _ODS_MAX_CONTENT_BYTES:
                raise ValueError("The spreadsheet is too large")
            root = ET.fromstring(archive.read("content.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"Could not read the file as an ODS spreadsheet: {exc}") from exc

    sheet = root.find(f".//{_ODS_TABLE}table")
    if sheet is None:
        raise ValueError("The spreadsheet has no sheet")

    table: _Table = []
    row_number = 0
    # iter() also walks into header-rows/row-groups, which hold rows too.
    for row in sheet.iter(f"{_ODS_TABLE}table-row"):
        cells: list[str] = []
        pending_blanks = 0
        for cell in row:
            if cell.tag not in (f"{_ODS_TABLE}table-cell", f"{_ODS_TABLE}covered-table-cell"):
                continue
            repeat = int(cell.get(f"{_ODS_TABLE}number-columns-repeated", "1"))
            value = _ods_cell_value(cell)
            if value.strip():
                cells.extend([""] * pending_blanks + [value] * repeat)
                pending_blanks = 0
            else:
                pending_blanks += repeat
        repeat = int(row.get(f"{_ODS_TABLE}number-rows-repeated", "1"))
        if cells:
            table.extend((row_number + i + 1, cells) for i in range(repeat))
        row_number += repeat
    return table


def _read_csv_or_ods(raw: bytes) -> _Table:
    # Sniffed by content, not by filename: an .ods is a zip ("PK" magic).
    return _read_ods(raw) if raw.startswith(b"PK\x03\x04") else _read_csv(raw)


def _students_from_table(table: _Table, columns: _RosterColumns) -> list[_ImportedStudentRow]:
    if not table:
        raise ValueError("The file has no header row")
    (_, header), *records = table
    index = {_header_key(name): i for i, name in reversed(list(enumerate(header))) if name.strip()}
    for required in (columns.student_id, columns.first_name):
        if _header_key(required) not in index:
            raise ValueError(f'The file is missing the required "{required}" column')

    def cell(cells: list[str], column: str | None) -> str:
        i = index.get(_header_key(column)) if column else None
        return cells[i].strip() if i is not None and i < len(cells) else ""

    rows: list[_ImportedStudentRow] = []
    for row_number, cells in records:
        student_id = cell(cells, columns.student_id)
        if not student_id:
            raise ValueError(
                f'Row {row_number}: missing required "{columns.student_id}" -- aborting import, nothing was saved'
            )
        first_name = cell(cells, columns.first_name)
        if not first_name:
            raise ValueError(
                f'Row {row_number}: missing required "{columns.first_name}" -- aborting import, nothing was saved'
            )
        last_name = cell(cells, columns.last_name)
        rows.append(
            _ImportedStudentRow(
                row_number=row_number,
                student_id=student_id,
                name=f"{first_name} {last_name}" if last_name else first_name,
                username=cell(cells, columns.username) or None,
                email=cell(cells, columns.email) or None,
            )
        )

    if not rows:
        raise ValueError("The file has no data rows")
    return rows


def _parse_atenea_csv(raw: bytes) -> list[_ImportedStudentRow]:
    return _students_from_table(_read_csv(raw), _ATENEA_COLUMNS)


def _parse_learnsql(raw: bytes) -> list[_ImportedStudentRow]:
    return _students_from_table(_read_csv_or_ods(raw), _LEARNSQL_COLUMNS)


_IMPORT_PARSERS: dict[StudentImportFormat, Callable[[bytes], list[_ImportedStudentRow]]] = {
    StudentImportFormat.ATENEA: _parse_atenea_csv,
    StudentImportFormat.LEARNSQL: _parse_learnsql,
}


class StudentImportResult(BaseModel):
    created: int
    updated: int
    students: list[StudentOut]


@router.post("/import", response_model=StudentImportResult)
async def import_students(
    file: UploadFile = File(...),
    format: StudentImportFormat = Form(...),
    session: AsyncSession = Depends(get_session),
) -> StudentImportResult:
    """Bulk-create/update students from an uploaded roster file (CSV, or ODS
    for `learnsql`).

    All rows are parsed and validated before anything is written: a single
    bad row (missing student id or first name, or a student id reused by two
    rows in the same file) 422s the whole import instead of leaving a
    half-applied roster. An existing student (matched by `student_id`) has
    `name`/`username`/`email` overwritten wholesale with the row's values --
    including clearing a field the row leaves blank -- rather than merged
    field by field.
    """
    parser = _IMPORT_PARSERS[format]
    try:
        rows = parser(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    seen: dict[str, int] = {}
    for row in rows:
        if row.student_id in seen:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate student id {row.student_id!r}: rows {seen[row.student_id]} and {row.row_number}",
            )
        seen[row.student_id] = row.row_number

    existing = set(
        await session.scalars(
            select(Student.student_id).where(Student.student_id.in_([row.student_id for row in rows]))
        )
    )
    stmt = insert(Student).values([row.model_dump() for row in rows])
    stmt = stmt.on_conflict_do_update(
        index_elements=[Student.student_id],
        set_={"name": stmt.excluded.name, "username": stmt.excluded.username, "email": stmt.excluded.email},
    ).returning(Student)
    students = list(await session.scalars(stmt, execution_options={"populate_existing": True}))
    await session.commit()

    return StudentImportResult(
        created=len(rows) - len(existing),
        updated=len(existing),
        students=[StudentOut.model_validate(s) for s in students],
    )
