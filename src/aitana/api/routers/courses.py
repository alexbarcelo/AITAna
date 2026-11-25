from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Course
from ...db.session import get_session
from ...slugify import slugify
from ..schemas import CourseOut

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseCreate(BaseModel):
    name: str
    slug: str | None = None


@router.get("", response_model=list[CourseOut])
async def list_courses(session: AsyncSession = Depends(get_session)) -> list[Course]:
    return list(await session.scalars(select(Course).order_by(Course.id)))


@router.post("", response_model=CourseOut, status_code=201)
async def create_course(payload: CourseCreate, session: AsyncSession = Depends(get_session)) -> Course:
    slug = payload.slug or slugify(payload.name)
    if await session.scalar(select(Course).where(Course.slug == slug)):
        raise HTTPException(status_code=409, detail=f"Course {slug!r} already exists")
    course = Course(name=payload.name, slug=slug)
    session.add(course)
    await session.commit()
    return course


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(course_id: int, session: AsyncSession = Depends(get_session)) -> Course:
    course = await session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course
