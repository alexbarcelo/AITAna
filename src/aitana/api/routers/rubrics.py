from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import RUBRIC_LOADS, Batch, Course, Edition, Rubric, Submission
from ...db.session import get_session
from ...grading.grading import grade_answer
from ...grading.llm import get_chat_model
from ...grading.models import Grade, SubmissionFormat
from ...slugify import slugify
from ..schemas import RubricIn, RubricOut
from .llm import MODEL_ID_QUERY, require_llm_model

router = APIRouter(prefix="/rubrics", tags=["rubrics"])


async def _get_rubric(session: AsyncSession, rubric_id: int, *, loaded: bool = False) -> Rubric:
    rubric = await session.get(Rubric, rubric_id, options=RUBRIC_LOADS if loaded else ())
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")
    return rubric


async def _check_slug_available(session: AsyncSession, slug: str, *, exclude_id: int | None = None) -> None:
    """409 if `slug` is already taken by a *different* rubric. `exclude_id`
    lets an update keep its own current slug (or re-save it unchanged)
    without tripping over itself."""
    existing = await session.scalar(select(Rubric.id).where(Rubric.slug == slug))
    if existing is not None and existing != exclude_id:
        raise HTTPException(status_code=409, detail=f"Rubric {slug!r} already exists")


async def _has_submissions(session: AsyncSession, rubric_id: int) -> bool:
    return bool(await session.scalar(select(exists().where(Submission.rubric_id == rubric_id))))


async def _guard_format_change(session: AsyncSession, rubric: Rubric, new_format: SubmissionFormat) -> None:
    """A rubric's `questions[].field` values only make sense under one
    format (see AGENTS.md's "Pluggable submission formats"), and a
    submission never records its own format -- it's implicitly whatever
    `rubric.format` was when it was graded. Letting an edit flip `format`
    under submissions already extracted/graded against the old one would
    make those submissions permanently unre-explainable. A rubric with no
    submissions yet is free to change format while it's being drafted."""
    if new_format == rubric.format:
        return
    if await _has_submissions(session, rubric.id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot change format from {rubric.format.value!r} to {new_format.value!r}: "
                "submissions already exist against this rubric."
            ),
        )


async def _resolve_course_edition(
    session: AsyncSession, course_id: int, edition_id: int | None
) -> tuple[Course, Edition | None]:
    course = await session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    edition = None
    if edition_id is not None:
        edition = await session.get(Edition, edition_id)
        if edition is None:
            raise HTTPException(status_code=404, detail="Edition not found")
    return course, edition


def _apply(rubric: Rubric, payload: RubricIn, slug: str, course: Course, edition: Edition | None) -> None:
    rubric.slug = slug
    rubric.title = payload.title
    rubric.course = course
    rubric.edition = edition
    rubric.format = payload.format
    rubric.grading_scale = payload.grading_scale
    rubric.context = payload.context
    rubric.general_feedback_instructions = (payload.general_feedback_instructions or "").strip() or None
    rubric.questions = payload.questions


async def _insert_rubric(session: AsyncSession, payload: RubricIn, slug: str) -> Rubric:
    course, edition = await _resolve_course_edition(session, payload.course_id, payload.edition_id)
    await _check_slug_available(session, slug)
    rubric = Rubric()
    _apply(rubric, payload, slug, course, edition)
    session.add(rubric)
    await session.commit()
    return rubric


async def _update_rubric(session: AsyncSession, rubric: Rubric, payload: RubricIn, slug: str) -> Rubric:
    """Update `rubric` in place -- shared by both edit paths (the manual-edit
    PUT and the "upload updated version" YAML PUT)."""
    course, edition = await _resolve_course_edition(session, payload.course_id, payload.edition_id)
    if slug != rubric.slug:
        await _check_slug_available(session, slug, exclude_id=rubric.id)
    await _guard_format_change(session, rubric, payload.format)
    _apply(rubric, payload, slug, course, edition)
    rubric.updated_at = datetime.now(UTC)
    await session.commit()
    return rubric


@router.get("", response_model=list[RubricOut])
async def list_rubrics(
    course_id: int | None = None,
    edition_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Rubric]:
    """Optionally filtered by the rubric's own course/edition (ANDed)."""
    query = select(Rubric).options(*RUBRIC_LOADS).order_by(Rubric.id)
    if course_id is not None:
        query = query.where(Rubric.course_id == course_id)
    if edition_id is not None:
        query = query.where(Rubric.edition_id == edition_id)
    return list(await session.scalars(query))


@router.get("/submission-counts", response_model=dict[str, int])
async def rubric_submission_counts(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """`{rubric_id: number of submissions}` for every rubric that has any
    (absent = 0), in one query rather than a count per rubric."""
    rows = await session.execute(select(Submission.rubric_id, func.count()).group_by(Submission.rubric_id))
    return {str(rubric_id): count for rubric_id, count in rows}


@router.get("/{rubric_id}", response_model=RubricOut)
async def get_rubric(rubric_id: int, session: AsyncSession = Depends(get_session)) -> Rubric:
    return await _get_rubric(session, rubric_id, loaded=True)


@router.post("", response_model=RubricOut, status_code=201)
async def create_rubric(payload: RubricIn, session: AsyncSession = Depends(get_session)) -> Rubric:
    """Create a rubric directly from a JSON body -- the "fill a form" path."""
    return await _insert_rubric(session, payload, payload.slug or slugify(payload.title))


@router.put("/{rubric_id}", response_model=RubricOut)
async def update_rubric(rubric_id: int, payload: RubricIn, session: AsyncSession = Depends(get_session)) -> Rubric:
    """Edit an existing rubric in place from a JSON body -- the "Build
    manually" edit path (same body shape as `POST /rubrics`). Unlike
    creation, a duplicate `slug` only 409s if it belongs to a *different*
    rubric -- resubmitting the form unchanged isn't a conflict with itself.
    """
    rubric = await _get_rubric(session, rubric_id, loaded=True)
    return await _update_rubric(session, rubric, payload, payload.slug or slugify(payload.title))


def _require_yaml_string(raw: dict, key: str) -> str | None:
    """Guard against a real YAML footgun: an unquoted scalar like `2026_27`
    is parsed by PyYAML as the *integer* 202627, not the string "2026_27" --
    YAML 1.1's int resolver treats `_` as a digit separator, so it silently
    swallows exactly the kind of slug an edition named "2026/27" gets. Surface
    a clear 422 pointing at the fix (quote it) instead of a baffling
    "Edition 202627 not found" 404 further down.
    """
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise HTTPException(
            status_code=422,
            detail=(
                f"`{key}` must be a YAML string, got {value!r} ({type(value).__name__}) -- "
                f'quote it if it looks like a number, e.g. `{key}: "{value}"`.'
            ),
        )
    return value


async def _parse_rubric_yaml(
    session: AsyncSession,
    yaml_file: UploadFile,
    slug: str | None,
    course_id: int | None,
    edition_id: int | None,
    format: SubmissionFormat | None,
    *,
    default_slug: str,
    honor_yaml_course_edition: bool = True,
) -> tuple[RubricIn, str]:
    """Shared YAML-body parsing for both `POST /rubrics/upload` (create) and
    `PUT /rubrics/{rubric_id}/upload` (the "upload updated version" edit
    flow). Returns the validated rubric body plus the resolved slug.

    Precedence for slug/course/edition/format: the form field, then the YAML
    key (`slug:`, `course_slug:`, `edition_slug:`, `format:`), then a
    default. `grading_scale:`, `context:` and `general_feedback_instructions:` are
    YAML-only.

    `honor_yaml_course_edition`: whether `course_slug:`/`edition_slug:` are
    consulted at all. False for the edit path: re-uploading a file must not
    move a rubric to another course/edition (see AGENTS.md's "Editing
    rubrics"), only the `course_id`/`edition_id` form fields can.
    """
    try:
        raw = yaml.safe_load((await yaml_file.read()).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse YAML: {exc}") from exc

    if not isinstance(raw, dict) or "questions" not in raw:
        raise HTTPException(status_code=422, detail="YAML must have a top-level `questions` list")

    course_slug = _require_yaml_string(raw, "course_slug") if honor_yaml_course_edition else None
    if course_id is None and course_slug:
        course_id = await session.scalar(select(Course.id).where(Course.slug == course_slug))
        if course_id is None:
            raise HTTPException(status_code=404, detail=f"Course {course_slug!r} not found")
    if course_id is None:
        detail = "A course is required: pass course_id"
        if honor_yaml_course_edition:
            detail += " or a course_slug: key in the YAML"
        raise HTTPException(status_code=422, detail=detail)

    edition_slug = _require_yaml_string(raw, "edition_slug") if honor_yaml_course_edition else None
    if edition_id is None and edition_slug:
        edition_id = await session.scalar(select(Edition.id).where(Edition.slug == edition_slug))
        if edition_id is None:
            raise HTTPException(status_code=404, detail=f"Edition {edition_slug!r} not found")

    resolved_slug = slug or _require_yaml_string(raw, "slug") or default_slug
    context = raw.get("context")
    if context is not None and not isinstance(context, str):
        raise HTTPException(status_code=422, detail="The YAML `context:` key must be a string")
    general_feedback_instructions = raw.get("general_feedback_instructions")
    if general_feedback_instructions is not None and not isinstance(general_feedback_instructions, str):
        raise HTTPException(status_code=422, detail="The YAML `general_feedback_instructions:` key must be a string")

    body: dict[str, Any] = {
        "title": raw.get("title") or resolved_slug.replace("_", " ").title(),
        "course_id": course_id,
        "edition_id": edition_id,
        "context": context,
        "general_feedback_instructions": general_feedback_instructions,
        "questions": raw["questions"],
    }
    if format is not None:
        body["format"] = format
    elif raw.get("format"):
        body["format"] = raw["format"]
    if raw.get("grading_scale") is not None:
        body["grading_scale"] = raw["grading_scale"]
    try:
        return RubricIn.model_validate(body), resolved_slug
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid rubric: {exc}") from exc


@router.post("/upload", response_model=RubricOut, status_code=201)
async def upload_rubric_yaml(
    yaml_file: UploadFile = File(...),
    slug: str | None = Form(None),
    course_id: int | None = Form(None),
    edition_id: int | None = Form(None),
    format: SubmissionFormat | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> Rubric:
    """Create a rubric from an uploaded YAML file (see README.md for the format)."""
    filename_stem = Path(yaml_file.filename or "rubric").stem.removesuffix("_questions")
    payload, resolved_slug = await _parse_rubric_yaml(
        session, yaml_file, slug, course_id, edition_id, format, default_slug=slugify(filename_stem)
    )
    return await _insert_rubric(session, payload, resolved_slug)


@router.put("/{rubric_id}/upload", response_model=RubricOut)
async def update_rubric_yaml(
    rubric_id: int,
    yaml_file: UploadFile = File(...),
    slug: str | None = Form(None),
    course_id: int | None = Form(None),
    edition_id: int | None = Form(None),
    format: SubmissionFormat | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> Rubric:
    """Edit an existing rubric in place by uploading a new YAML file -- the
    "upload updated version" edit path. Same parsing as `POST
    /rubrics/upload`, except:
    - the slug (if not overridden by the form field or a `slug:` YAML key)
      defaults to the rubric's *current* slug rather than being re-derived
      from the uploaded filename.
    - a `course_slug:`/`edition_slug:` key in the file is ignored; the course
      and edition stay as they are unless the `course_id`/`edition_id` form
      fields say otherwise.
    """
    rubric = await _get_rubric(session, rubric_id, loaded=True)
    payload, resolved_slug = await _parse_rubric_yaml(
        session,
        yaml_file,
        slug,
        course_id if course_id is not None else rubric.course_id,
        edition_id if edition_id is not None else rubric.edition_id,
        format,
        default_slug=rubric.slug,
        honor_yaml_course_edition=False,
    )
    return await _update_rubric(session, rubric, payload, resolved_slug)


@router.delete("/{rubric_id}", status_code=204)
async def delete_rubric(rubric_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Delete a rubric outright. Blocked with `409` if any submission or
    batch references it (the foreign keys would refuse anyway; checking
    first gives a message that says why). A rubric nobody has used yet
    deletes freely."""
    rubric = await _get_rubric(session, rubric_id)
    if await _has_submissions(session, rubric.id):
        raise HTTPException(status_code=409, detail="Cannot delete: submissions already exist against this rubric.")
    if await session.scalar(select(exists().where(Batch.rubric_id == rubric.id))):
        raise HTTPException(status_code=409, detail="Cannot delete: batches already exist against this rubric.")
    await session.delete(rubric)
    await session.commit()


class TestAnswerRequest(BaseModel):
    question_id: str
    answer: str


@router.post("/{rubric_id}/test-answer", response_model=Grade)
async def test_rubric_answer(
    rubric_id: int,
    payload: TestAnswerRequest,
    model_id: int | None = MODEL_ID_QUERY,
    session: AsyncSession = Depends(get_session),
) -> Grade:
    """Grade one free-typed answer against a single question of this rubric,
    synchronously, with no submission, file or background job involved --
    lets a rubric author try out wording changes and see the LLM's actual
    response. Nothing here is persisted.
    """
    rubric = await _get_rubric(session, rubric_id)

    question = next((q for q in rubric.questions if q.id == payload.question_id), None)
    if question is None:
        raise HTTPException(status_code=404, detail=f"Question {payload.question_id!r} not found in this rubric")

    llm_model = await require_llm_model(session, model_id)
    try:
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
        # grade_answer() makes a blocking network call to the LLM -- unlike
        # the batch pipeline (worker/tasks.py), which runs it in a separate
        # Celery process, this is a single quick "try it" call, so a
        # threadpool hop off the event loop is enough.
        return await run_in_threadpool(
            partial(grade_answer, rubric_context=rubric.context),
            chat_model,
            question,
            payload.answer,
            rubric.grading_scale,
        )
    except Exception as exc:  # noqa: BLE001 -- surface any LLM/config failure as a clean error, not a raw 500
        raise HTTPException(status_code=502, detail=f"Grading failed: {exc}") from exc
