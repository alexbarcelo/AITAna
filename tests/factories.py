"""Helpers for tests that set up rows directly instead of through the API."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from aitana.db import Course, Edition, Rubric, Student, Submission
from aitana.grading.models import Question


async def add(db: AsyncSession, *objects):
    """Insert and commit `objects`; returns the first one (or all of them)."""
    db.add_all(objects)
    await db.commit()
    return objects[0] if len(objects) == 1 else objects


def question(qid: str = "answer1", **overrides) -> Question:
    fields = {"id": qid, "title": qid.upper(), "question": f"{qid}?", "rubric": f"Rubric for {qid}"}
    fields.update(overrides)
    return Question(**fields)


async def make_course(db: AsyncSession, slug: str = "bdm") -> Course:
    return await add(db, Course(name=slug.upper(), slug=slug))


async def make_edition(db: AsyncSession, slug: str = "2026_27") -> Edition:
    return await add(db, Edition(name=slug.replace("_", "/"), slug=slug))


async def make_rubric(db: AsyncSession, slug: str = "containers", *, course: Course | None = None, **fields) -> Rubric:
    course = course or await make_course(db, f"course_{slug}")
    fields.setdefault("questions", [question()])
    return await add(db, Rubric(slug=slug, title=slug.title(), course=course, **fields))


async def make_submission(
    db: AsyncSession,
    *,
    rubric: Rubric | None = None,
    edition: Edition | None = None,
    student: Student | None = None,
    **fields,
) -> Submission:
    rubric = rubric or await make_rubric(db)
    edition = edition or await make_edition(db, f"ed_{rubric.slug}")
    fields.setdefault("file_object_key", "k")
    return await add(db, Submission(rubric=rubric, edition=edition, student=student, **fields))


async def save(db: AsyncSession, obj) -> None:
    """Commit changes made to `obj`, including in-place edits of its JSONB
    columns (which the ORM can't see on its own -- see `PydanticJSONB`)."""
    for attr in ("answers", "document", "segments", "questions", "grading_scale"):
        if hasattr(type(obj), attr):
            flag_modified(obj, attr)
    await db.commit()


async def reload(db: AsyncSession, model, obj_id):
    """`obj_id`'s row as currently stored (bypassing the session's cache)."""
    return await db.get(model, obj_id, populate_existing=True)
