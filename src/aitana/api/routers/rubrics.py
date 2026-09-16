from datetime import UTC, datetime
from pathlib import Path

import yaml
from beanie import PydanticObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

from ...documents import Batch, Course, Edition, Rubric, Submission
from ...grading.grading import grade_answer
from ...grading.llm import get_chat_model
from ...grading.models import DEFAULT_GRADING_SCALE, Grade, Question, SubmissionFormat
from ...settings import get_settings
from ...slugify import slugify

router = APIRouter(prefix="/rubrics", tags=["rubrics"])

# No depth cap needed on `Rubric.course`/`.edition` here (unlike
# Submission.rubric, see submissions.py's _SHALLOW_LINKS): Course and
# Edition are both plain documents with no Link fields of their own, so
# resolving either fully via fetch_links never produces a nested-pipeline
# $lookup for mongomock to choke on (AGENTS.md sharp edge #6).


class RubricCreate(BaseModel):
    title: str
    slug: str | None = None
    course_id: PydanticObjectId
    edition_id: PydanticObjectId | None = None
    # Which file format this rubric's submissions will be uploaded as --
    # fixed at creation time (see Rubric.format's docstring). Defaults to
    # `pdf`, this project's original/only format, so existing callers that
    # don't send this field keep working unchanged.
    format: SubmissionFormat = SubmissionFormat.PDF
    # {level_identifier: description}, worst -> best (see
    # Rubric.grading_scale's docstring). Defaults to the original 4-level
    # scale.
    grading_scale: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GRADING_SCALE))
    questions: list[Question]


async def _check_slug_available(slug: str, *, exclude_id: PydanticObjectId | None = None) -> None:
    """409 if `slug` is already taken by a *different* rubric. `exclude_id`
    lets an update keep its own current slug (or re-save it unchanged)
    without tripping over itself."""
    existing = await Rubric.find_one(Rubric.slug == slug)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status_code=409, detail=f"Rubric {slug!r} already exists")


async def _guard_format_change(rubric: Rubric, new_format: SubmissionFormat) -> None:
    """A rubric's `questions[].field` values only make sense under one
    format (see Rubric.format's docstring / AGENTS.md's "Pluggable
    submission formats"), and a submission never records its own format --
    it's implicitly whatever `rubric.format` was when it was graded. Letting
    an edit silently flip `format` out from under submissions already
    extracted/graded against the old one would make those submissions
    permanently unre-explainable (their stored answers were read under a
    field-naming convention the rubric no longer documents). Block it
    outright rather than guessing; a rubric with no submissions yet is free
    to change format as many times as needed while it's still being drafted.
    """
    if new_format == rubric.format:
        return
    # .to_list() rather than .count(): the same `Submission.rubric.id == ...`
    # filter shape is already relied on by api/routers/submissions.py's
    # list_submissions -- .count() runs a separate aggregation path that
    # isn't exercised the same way. Per AGENTS.md sharp edge #3, this
    # `Link.id ==` filter is only *positively* verified against real
    # MongoDB, not mongomock (which returns zero matches for it regardless
    # of whether one exists -- a mongomock DBRef-matching limitation, not a
    # Beanie/app bug); see tests/test_rubrics_api.py's comment by the
    # format-change tests for why there's no positive-match unit test here.
    if await Submission.find(Submission.rubric.id == rubric.id).to_list():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot change format from {rubric.format.value!r} to {new_format.value!r}: "
                "submissions already exist against this rubric."
            ),
        )


async def _insert_rubric(
    slug: str,
    title: str,
    questions: list[Question],
    course: Course,
    edition: Edition | None,
    fmt: SubmissionFormat,
    grading_scale: dict[str, str],
) -> Rubric:
    await _check_slug_available(slug)
    try:
        # Rubric's own validators (e.g. grading_scale must be non-empty)
        # aren't FastAPI request-body validation -- catch them here so both
        # creation paths (the JSON body above and the YAML upload below,
        # which builds grading_scale from a free-form YAML value) 422
        # instead of 500ing on a bad value.
        rubric = Rubric(
            slug=slug, title=title, course=course, edition=edition, format=fmt, grading_scale=grading_scale, questions=questions
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid rubric: {exc}") from exc
    await rubric.insert()
    return rubric


async def _apply_rubric_update(
    rubric: Rubric,
    slug: str,
    title: str,
    questions: list[Question],
    course: Course,
    edition: Edition | None,
    fmt: SubmissionFormat,
    grading_scale: dict[str, str],
) -> Rubric:
    """Update `rubric` in place with new field values -- shared by both edit
    paths (the manual-edit PUT and the "upload updated version" YAML PUT)."""
    if slug != rubric.slug:
        await _check_slug_available(slug, exclude_id=rubric.id)
    await _guard_format_change(rubric, fmt)

    rubric.slug = slug
    rubric.title = title
    rubric.course = course
    rubric.edition = edition
    rubric.format = fmt
    rubric.grading_scale = grading_scale
    rubric.questions = questions
    rubric.updated_at = datetime.now(UTC)
    try:
        # Beanie's Document.save() re-validates the whole document (same
        # `validate_on_save` behavior worker/tasks.py's _grade_answers relies
        # on) -- this is what actually re-runs Rubric's grading_scale
        # validator, since plain attribute assignment above doesn't.
        await rubric.save()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid rubric: {exc}") from exc
    return rubric


@router.get("", response_model=list[Rubric])
async def list_rubrics() -> list[Rubric]:
    return await Rubric.find(fetch_links=True).to_list()


@router.get("/{rubric_id}", response_model=Rubric)
async def get_rubric(rubric_id: PydanticObjectId) -> Rubric:
    rubric = await Rubric.get(rubric_id, fetch_links=True)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")
    return rubric


@router.post("", response_model=Rubric, status_code=201)
async def create_rubric(payload: RubricCreate) -> Rubric:
    """Create a rubric directly from a JSON body -- the "fill a form" path."""
    course = await Course.get(payload.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    edition = None
    if payload.edition_id is not None:
        edition = await Edition.get(payload.edition_id)
        if edition is None:
            raise HTTPException(status_code=404, detail="Edition not found")

    slug = payload.slug or slugify(payload.title)
    return await _insert_rubric(slug, payload.title, payload.questions, course, edition, payload.format, payload.grading_scale)


@router.put("/{rubric_id}", response_model=Rubric)
async def update_rubric(rubric_id: PydanticObjectId, payload: RubricCreate) -> Rubric:
    """Edit an existing rubric in place from a JSON body -- the "Build
    manually" edit path (same body shape as `POST /rubrics`, see
    `RubricCreate`). Unlike creation, a duplicate `slug` only 409s if it
    belongs to a *different* rubric -- resubmitting the form unchanged (or
    changing everything except the slug) isn't a conflict with itself.
    """
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")

    course = await Course.get(payload.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    edition = None
    if payload.edition_id is not None:
        edition = await Edition.get(payload.edition_id)
        if edition is None:
            raise HTTPException(status_code=404, detail="Edition not found")

    slug = payload.slug or slugify(payload.title)
    return await _apply_rubric_update(
        rubric, slug, payload.title, payload.questions, course, edition, payload.format, payload.grading_scale
    )


def _require_yaml_string(raw: dict, key: str) -> str | None:
    """Guard against a real YAML footgun: an unquoted scalar like `2026_27`
    is parsed by PyYAML as the *integer* 202627, not the string "2026_27" --
    YAML 1.1's int resolver treats `_` as a digit separator, so it silently
    swallows exactly the kind of slug a global edition ends up with (see
    Edition.slug, and its docstring's history for why edition slugs no
    longer have a non-numeric course prefix to save them from this). Surface
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
    yaml_file: UploadFile,
    slug: str | None,
    course_id: PydanticObjectId | None,
    edition_id: PydanticObjectId | None,
    format: SubmissionFormat | None,
    *,
    default_slug: str,
    honor_yaml_course_edition: bool = True,
) -> tuple[str, str, list[Question], Course, Edition | None, SubmissionFormat, dict[str, str]]:
    """Shared YAML-body parsing for both `POST /rubrics/upload` (create) and
    `PUT /rubrics/{rubric_id}/upload` (the "upload updated version" edit
    flow) -- everything about interpreting the file/form fields is identical
    between the two; only what `default_slug` falls back to (and what
    happens with the result) differs, plus `honor_yaml_course_edition` (see
    below).

    `honor_yaml_course_edition`: whether a `course_slug:`/`edition_slug:` key
    in the YAML body is even consulted for resolving course/edition. True
    for creation (the whole point of those keys -- bulk-importing
    `configs/*.yaml` files that carry them). False for the edit path: a
    rubric's course/edition aren't something re-uploading a YAML file should
    be able to change (see AGENTS.md's "Editing rubrics" -- the frontend
    locks these fields during edit for the same reason), so only the
    `course_id`/`edition_id` form fields can move them, same as those two
    already take precedence over the YAML keys during creation too.
    """
    try:
        raw = yaml.safe_load((await yaml_file.read()).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse YAML: {exc}") from exc

    if not isinstance(raw, dict) or "questions" not in raw:
        raise HTTPException(status_code=422, detail="YAML must have a top-level `questions` list")

    try:
        questions = [Question(**q) for q in raw["questions"]]
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid question(s): {exc}") from exc

    course_slug = _require_yaml_string(raw, "course_slug") if honor_yaml_course_edition else None
    course: Course | None = None
    if course_id is not None:
        course = await Course.get(course_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found")
    elif course_slug:
        course = await Course.find_one(Course.slug == course_slug)
        if course is None:
            raise HTTPException(status_code=404, detail=f"Course {course_slug!r} not found")
    if course is None:
        detail = "A course is required: pass course_id"
        if honor_yaml_course_edition:
            detail += " or a course_slug: key in the YAML"
        raise HTTPException(status_code=422, detail=detail)

    edition_slug = _require_yaml_string(raw, "edition_slug") if honor_yaml_course_edition else None
    edition: Edition | None = None
    if edition_id is not None:
        edition = await Edition.get(edition_id)
        if edition is None:
            raise HTTPException(status_code=404, detail="Edition not found")
    elif edition_slug:
        edition = await Edition.find_one(Edition.slug == edition_slug)
        if edition is None:
            raise HTTPException(status_code=404, detail=f"Edition {edition_slug!r} not found")

    resolved_slug = slug or _require_yaml_string(raw, "slug") or default_slug
    title = raw.get("title") or resolved_slug.replace("_", " ").title()

    # Same "form field, then a YAML key, then a default" precedence as
    # slug/course/edition above. Unlike course/edition, a bad value here
    # (a typo'd string) is validated by SubmissionFormat(...) rather than a
    # database lookup -- still a 422, not a 500.
    if format is not None:
        resolved_format = format
    elif raw.get("format"):
        try:
            resolved_format = SubmissionFormat(raw["format"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid format {raw['format']!r}: {exc}") from exc
    else:
        resolved_format = SubmissionFormat.PDF

    # No form-field equivalent (a dict doesn't fit a multipart field
    # cleanly) -- just the YAML key, else the default. Shape/emptiness
    # validation (must be a {level: description} mapping, non-empty) happens
    # inside _insert_rubric's/_apply_rubric_update's Rubric construction, not
    # here.
    grading_scale = raw["grading_scale"] if raw.get("grading_scale") is not None else dict(DEFAULT_GRADING_SCALE)

    return resolved_slug, title, questions, course, edition, resolved_format, grading_scale


@router.post("/upload", response_model=Rubric, status_code=201)
async def upload_rubric_yaml(
    yaml_file: UploadFile = File(...),
    slug: str | None = Form(None),
    course_id: PydanticObjectId | None = Form(None),
    edition_id: PydanticObjectId | None = Form(None),
    format: SubmissionFormat | None = Form(None),
) -> Rubric:
    """Create a rubric from an uploaded YAML file (see README.md for the format)."""
    filename_stem = Path(yaml_file.filename or "rubric").stem.removesuffix("_questions")
    resolved_slug, title, questions, course, edition, resolved_format, grading_scale = await _parse_rubric_yaml(
        yaml_file, slug, course_id, edition_id, format, default_slug=slugify(filename_stem)
    )
    return await _insert_rubric(resolved_slug, title, questions, course, edition, resolved_format, grading_scale)


@router.put("/{rubric_id}/upload", response_model=Rubric)
async def update_rubric_yaml(
    rubric_id: PydanticObjectId,
    yaml_file: UploadFile = File(...),
    slug: str | None = Form(None),
    course_id: PydanticObjectId | None = Form(None),
    edition_id: PydanticObjectId | None = Form(None),
    format: SubmissionFormat | None = Form(None),
) -> Rubric:
    """Edit an existing rubric in place by uploading a new YAML file -- the
    "upload updated version" edit path. Same parsing as `POST
    /rubrics/upload`, except:
    - the slug (if not overridden by the form field or a `slug:` YAML key)
      defaults to the rubric's *current* slug rather than being re-derived
      from the uploaded filename -- re-uploading a differently-named file to
      update an existing rubric shouldn't change its identity as a side
      effect.
    - a `course_slug:`/`edition_slug:` key in the file is ignored
      (`honor_yaml_course_edition=False`) -- a rubric's course/edition
      aren't editable via this flow at all (see `_parse_rubric_yaml`'s
      docstring), only via the `course_id`/`edition_id` form fields.
    """
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")

    resolved_slug, title, questions, course, edition, resolved_format, grading_scale = await _parse_rubric_yaml(
        yaml_file, slug, course_id, edition_id, format, default_slug=rubric.slug, honor_yaml_course_edition=False
    )
    return await _apply_rubric_update(rubric, resolved_slug, title, questions, course, edition, resolved_format, grading_scale)


@router.delete("/{rubric_id}", status_code=204)
async def delete_rubric(rubric_id: PydanticObjectId) -> None:
    """Delete a rubric outright. Blocked with `409` if any `Submission` or
    `Batch` already references it -- deleting out from under those would
    leave them pointing at nothing (`Submission.rubric`/`Batch.rubric` are
    both required `Link[Rubric]` fields, and every submission/batch view
    reads `rubric.slug`/`.title`/`.questions` to render at all), silently
    breaking history that already exists rather than just losing an unused
    definition. A rubric with no submissions/batches yet (still being
    drafted, or never actually used) deletes freely.
    """
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")

    # Same `Link.id == ...` filter shape as `_guard_format_change` -- see
    # its comment (and AGENTS.md's sharp edge #3) for why there's no
    # positive-match unit test for this under mongomock.
    if await Submission.find(Submission.rubric.id == rubric.id).to_list():
        raise HTTPException(status_code=409, detail="Cannot delete: submissions already exist against this rubric.")
    if await Batch.find(Batch.rubric.id == rubric.id).to_list():
        raise HTTPException(status_code=409, detail="Cannot delete: batches already exist against this rubric.")

    await rubric.delete()


class TestAnswerRequest(BaseModel):
    question_id: str
    answer: str


@router.post("/{rubric_id}/test-answer", response_model=Grade)
async def test_rubric_answer(rubric_id: PydanticObjectId, payload: TestAnswerRequest) -> Grade:
    """Grade one free-typed answer against a single question of this rubric,
    synchronously, with no Submission/file/MinIO/Celery involved -- lets a
    rubric author try out wording changes and see the LLM's actual response
    before wiring up a real exam upload. Nothing here is persisted.
    """
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")

    question = next((q for q in rubric.questions if q.id == payload.question_id), None)
    if question is None:
        raise HTTPException(status_code=404, detail=f"Question {payload.question_id!r} not found in this rubric")

    settings = get_settings()
    try:
        chat_model = get_chat_model(settings.llm_provider, settings.llm_model)
        # grade_answer() makes a blocking network call to the LLM -- unlike
        # the batch pipeline (worker/tasks.py), which isolates that to a
        # separate Celery process entirely, this is a single quick "try it"
        # call, so a threadpool hop off the event loop is enough rather than
        # standing up a task+poll flow for one question.
        return await run_in_threadpool(grade_answer, chat_model, question, payload.answer, rubric.grading_scale)
    except Exception as exc:  # noqa: BLE001 -- surface any LLM/config failure as a clean error, not a raw 500
        raise HTTPException(status_code=502, detail=f"Grading failed: {exc}") from exc
