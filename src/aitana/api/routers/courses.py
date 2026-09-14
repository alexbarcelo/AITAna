from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...documents import Course
from ...slugify import slugify

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseCreate(BaseModel):
    name: str
    slug: str | None = None


@router.get("", response_model=list[Course])
async def list_courses() -> list[Course]:
    return await Course.find_all().to_list()


@router.post("", response_model=Course, status_code=201)
async def create_course(payload: CourseCreate) -> Course:
    slug = payload.slug or slugify(payload.name)
    if await Course.find_one(Course.slug == slug):
        raise HTTPException(status_code=409, detail=f"Course {slug!r} already exists")
    course = Course(name=payload.name, slug=slug)
    await course.insert()
    return course


@router.get("/{course_id}", response_model=Course)
async def get_course(course_id: PydanticObjectId) -> Course:
    course = await Course.get(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course
