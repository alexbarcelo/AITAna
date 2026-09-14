from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException
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
