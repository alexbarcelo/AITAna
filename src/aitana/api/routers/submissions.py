import base64
import logging
from pathlib import PurePosixPath
from urllib.parse import quote
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from ... import storage
from ...db import (
    SUBMISSION_LOADS,
    Edition,
    Rubric,
    SegmentationSource,
    Student,
    Submission,
    SubmissionStatus,
)
from ...db.session import get_session
from ...grading.extraction import (
    FORMAT_FILE_INFO,
    SEGMENTED_FORMATS,
    UPLOAD_CONTENT_TYPES,
    accepted_upload_extensions,
)
from ...grading.extraction.generic_document import (
    DocumentConversionError,
    SegmentationError,
    document_to_pdf,
    missing_questions,
    validate_segments,
)
from ...grading.models import AnswerSegment, Grade, SubmissionFormat
from ...settings import get_settings
from ...worker.tasks import extract_submission_file, grade_submission, prepare_submission, suggest_segments_task
from ..feedback_export import course_name, feedback_filename, load_answer_images, render_feedback_html
from ..schemas import SubmissionOut
from .llm import MODEL_ID_QUERY, require_llm_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/submissions", tags=["submissions"])


async def get_submission_or_404(session: AsyncSession, submission_id: int, *, loaded: bool = True) -> Submission:
    """`loaded` eager-loads what `SubmissionOut` (and the file/feedback
    endpoints) need: student, rubric, edition."""
    submission = await session.get(Submission, submission_id, options=SUBMISSION_LOADS if loaded else ())
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


def enqueue_grading(submission: Submission, model_id: int, question_id: str | None = None) -> None:
    """`model_id` is always resolved by the caller (`require_llm_model`)
    before enqueueing, so a missing default fails the request, not the job,
    and the job grades with the model that was active when it was asked for."""
    grade_submission.delay(submission.id, only_question_id=question_id, model_id=model_id)


async def enqueue_regrades(
    session: AsyncSession, submissions: list[Submission], model_id: int, question_id: str | None = None
) -> None:
    """Reset submissions to `pending` and re-enqueue grading -- all of it,
    or only `question_id` (other answers keep their grades, see
    worker/tasks.py's `_grade_submission`). Commits before enqueueing, so
    the worker never reads the pre-reset row."""
    for submission in submissions:
        submission.status = SubmissionStatus.PENDING
        submission.error = None
    await session.commit()
    for submission in submissions:
        enqueue_grading(submission, model_id, question_id)


def enqueue_initial_processing(
    submission: Submission, rubric: Rubric, model_id: int | None, llm_segmentation: bool
) -> None:
    """What happens right after upload: grading, or -- for a generic document
    rubric (`SEGMENTED_FORMATS`) -- conversion plus, if asked, an LLM
    segmentation suggestion. Grading those waits for a professor."""
    if rubric.format in SEGMENTED_FORMATS:
        prepare_submission.delay(submission.id, segment_model_id=model_id if llm_segmentation else None)
    else:
        assert model_id is not None
        enqueue_grading(submission, model_id)


async def upload_model(
    session: AsyncSession, rubric: Rubric, model_id: int | None, llm_segmentation: bool
) -> int | None:
    """The model an upload needs resolved up front: always for formats
    graded right away, only with LLM-assisted segmentation for free-form
    PDFs (manual segmentation needs no LLM until grading)."""
    if rubric.format in SEGMENTED_FORMATS and not llm_segmentation:
        return None
    return (await require_llm_model(session, model_id)).id


def upload_extension(fmt: SubmissionFormat, filename: str) -> str | None:
    """Which of the rubric format's accepted upload extensions (its stored
    one, plus any in `UPLOAD_CONVERSIONS`) `filename` has, or `None`. Only a
    courtesy check on the name, not a content sniff (see AGENTS.md
    "Pluggable submission formats")."""
    name = filename.lower()
    return next((ext for ext in accepted_upload_extensions(fmt) if name.endswith(ext)), None)


def expected_extensions_text(fmt: SubmissionFormat) -> str:
    return " or ".join(accepted_upload_extensions(fmt))


async def to_stored_format(fmt: SubmissionFormat, extension: str, data: bytes, filename: str) -> bytes:
    """The bytes to store for an upload: as-is when already in the format's
    stored extension, else converted (e.g. `.docx` -> PDF for a generic
    document rubric). Conversion is CPU-bound, so it runs off the event
    loop; a file that can't be converted is a `422` naming it."""
    if extension == FORMAT_FILE_INFO[fmt][0]:
        return data
    settings = get_settings()
    try:
        return await run_in_threadpool(
            document_to_pdf,
            data,
            extension,
            gotenberg_url=settings.gotenberg_url,
            timeout=settings.gotenberg_timeout,
        )
    except DocumentConversionError as exc:
        raise HTTPException(status_code=422, detail=f"{filename!r}: {exc}") from exc


def original_upload_name(filename: str | None) -> str | None:
    """The uploaded file's own name, without any client-side directory part."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    return name or None


def store_original_upload(submission: Submission, fmt: SubmissionFormat, data: bytes, extension: str) -> None:
    """Keep the untouched uploaded bytes when they were converted before
    storing (`to_stored_format`), for provenance -- next to the stored file,
    in its derived folder, so `storage.delete_submission_files` removes them
    too. A no-op for an upload stored as-is (the stored file *is* the
    original). Needs `submission.file_object_key` already set; the caller
    saves the submission."""
    if extension == FORMAT_FILE_INFO[fmt][0]:
        return
    key = storage.derived_object_key(submission.file_object_key, f"original{extension}")
    storage.upload_file(key, data, UPLOAD_CONTENT_TYPES.get(extension, "application/octet-stream"))
    submission.original_object_key = key


def content_disposition(disposition: str, filename: str) -> str:
    """`Content-Disposition` value that survives a non-ASCII filename (an
    original upload's name may have accents): an ASCII fallback plus the
    RFC 5987 `filename*` form, which browsers prefer."""
    fallback = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_").replace("\\", "_")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


async def resolve_edition(session: AsyncSession, rubric: Rubric, edition_id: int | None) -> Edition:
    """A submission always has a definite edition, even for a rubric that
    doesn't fix one (e.g. a lab reused across years): use the rubric's own
    edition if it has one (any `edition_id` given is ignored), otherwise the
    uploader must supply one."""
    if rubric.edition_id is not None:
        edition_id = rubric.edition_id
    elif edition_id is None:
        raise HTTPException(
            status_code=422,
            detail="This rubric has no fixed edition -- edition_id is required",
        )
    edition = await session.get(Edition, edition_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return edition


async def get_rubric_or_404(session: AsyncSession, rubric_id: int) -> Rubric:
    rubric = await session.get(Rubric, rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")
    return rubric


@router.post("", response_model=SubmissionOut, status_code=202)
async def create_submission(
    file: UploadFile = File(...),
    student_id: int | None = Form(None),
    label: str | None = Form(None),
    rubric_id: int = Form(...),
    edition_id: int | None = Form(None),
    llm_segmentation: bool = Form(True),
    model_id: int | None = MODEL_ID_QUERY,
    session: AsyncSession = Depends(get_session),
) -> Submission:
    """Either `student_id` (a roster student) or `label` (a labelled
    submission, e.g. a group project -- see `Submission.label`) is
    required; giving both is a `422` too, since a submission is one or the
    other.

    `llm_segmentation` only matters for generic-document rubrics: whether to
    ask the LLM for a segmentation suggestion right after conversion."""
    label = (label or "").strip() or None
    if (student_id is None) == (label is None):
        raise HTTPException(
            status_code=422,
            detail="Give either a student_id or a label (for a labelled submission), not both",
        )
    student = None
    if student_id is not None:
        student = await session.get(Student, student_id)
        if student is None:
            raise HTTPException(status_code=404, detail="Student not found")
    rubric = await get_rubric_or_404(session, rubric_id)
    resolved_model_id = await upload_model(session, rubric, model_id, llm_segmentation)
    edition = await resolve_edition(session, rubric, edition_id)

    # The rubric, not the uploader, decides what format its submissions are
    # in (see Rubric.format's docstring) -- this only catches an obvious
    # mismatch (wrong file picked in the form) early, as a 422 rather than a
    # confusing extraction failure once grading starts.
    # Other accepted extensions (e.g. .docx for a generic document) are
    # converted to the stored one before anything is written.
    extension, content_type = FORMAT_FILE_INFO[rubric.format]
    filename = file.filename or ""
    uploaded_extension = upload_extension(rubric.format, filename) if filename else extension
    if uploaded_extension is None:
        raise HTTPException(
            status_code=422,
            detail=f"This rubric expects a {expected_extensions_text(rubric.format)} file "
            f"({rubric.format.value}), got {filename!r}",
        )
    raw = await file.read()
    data = await to_stored_format(rubric.format, uploaded_extension, raw, filename)

    submission = Submission(
        student=student,
        label=label,
        rubric=rubric,
        edition=edition,
        original_filename=original_upload_name(filename),
    )
    session.add(submission)
    # The id is part of the storage key: flush to get it, store the file,
    # then commit -- a storage failure leaves no row behind.
    await session.flush()

    if student is not None:
        key = storage.object_key(rubric.slug, student.student_id, str(submission.id), extension)
    else:
        key = storage.labelled_object_key(rubric.slug, str(submission.id), extension)
    storage.upload_file(key, data, content_type)
    submission.file_object_key = key
    store_original_upload(submission, rubric.format, raw, uploaded_extension)
    await session.commit()

    enqueue_initial_processing(submission, rubric, resolved_model_id, llm_segmentation)
    return submission


@router.get("", response_model=list[SubmissionOut])
async def list_submissions(
    student_id: int | None = None,
    rubric_id: int | None = None,
    course_id: int | None = None,
    edition_id: int | None = None,
    batch_id: int | None = None,
    status: SubmissionStatus | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Submission]:
    """All filters AND together. `course_id` goes through the rubric (a
    submission's course *is* its rubric's course); `edition_id` is the
    submission's own edition."""
    query = select(Submission).options(*SUBMISSION_LOADS).order_by(Submission.id)
    if student_id is not None:
        query = query.where(Submission.student_id == student_id)
    if rubric_id is not None:
        query = query.where(Submission.rubric_id == rubric_id)
    if batch_id is not None:
        query = query.where(Submission.batch_id == batch_id)
    if status is not None:
        query = query.where(Submission.status == status)
    if course_id is not None:
        query = query.join(Rubric, Submission.rubric_id == Rubric.id).where(Rubric.course_id == course_id)
    if edition_id is not None:
        query = query.where(Submission.edition_id == edition_id)
    return list(await session.scalars(query))


@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: int, session: AsyncSession = Depends(get_session)) -> Submission:
    return await get_submission_or_404(session, submission_id)


class SetSubmissionStudent(BaseModel):
    student_id: int


@router.put("/{submission_id}/student", response_model=SubmissionOut)
async def set_submission_student(
    submission_id: int, payload: SetSubmissionStudent, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Manually set (or correct) which student a submission belongs to --
    mainly for a batch-created submission whose folder name didn't
    auto-match anyone, or matched the wrong person (see
    api/routers/batches.py's `_match_student`), but works for any
    submission."""
    submission = await get_submission_or_404(session, submission_id)
    student = await session.get(Student, payload.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    submission.student = student
    submission.updated_at = datetime.now(UTC)
    await session.commit()
    return submission


class SetSubmissionLabel(BaseModel):
    label: str


@router.put("/{submission_id}/label", response_model=SubmissionOut)
async def set_submission_label(
    submission_id: int, payload: SetSubmissionLabel, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Rename a labelled submission's `label` (e.g. fix a typo in a group
    name). Same blank-is-none rule as `create_submission`, but a blank label
    is a `422` here rather than a way to clear it -- a labelled submission
    always needs some identifier."""
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="Label can't be blank")
    submission = await get_submission_or_404(session, submission_id)
    submission.label = label
    submission.updated_at = datetime.now(UTC)
    await session.commit()
    return submission


_IN_PROGRESS_STATUSES = {
    SubmissionStatus.PENDING,
    SubmissionStatus.CONVERTING,
    SubmissionStatus.SEGMENTING,
    SubmissionStatus.EXTRACTING,
    SubmissionStatus.GRADING,
}


def guard_not_in_progress(submissions: list[Submission], force: bool) -> None:
    """`409` if any of these is still being worked on, unless `force`: the
    worker would fail mid-run on a row (and files) that no longer exist.
    `force` exists for jobs that were lost and will never finish (same
    situation as the frontend's "still in progress (or stuck)" regrade
    confirmation)."""
    busy = [s for s in submissions if s.status in _IN_PROGRESS_STATUSES]
    if busy and not force:
        what = "This submission is" if len(submissions) == 1 else f"{len(busy)} submission(s) are"
        raise HTTPException(
            status_code=409, detail=f"{what} still being processed; pass force=true to delete anyway."
        )


async def delete_submissions(session: AsyncSession, submissions: list[Submission]) -> None:
    """Delete submissions (their grading traces go with them, cascading
    foreign key) and their stored files. The database goes first and is
    committed: a storage failure afterwards only leaves orphaned objects
    (logged), never a submission pointing at a missing file."""
    for submission in submissions:
        await session.delete(submission)
    await session.commit()
    for submission in submissions:
        try:
            storage.delete_submission_files(submission.file_object_key)
        except Exception:
            logger.exception("Could not delete stored files for submission %s", submission.id)


@router.delete("/{submission_id}", status_code=204)
async def delete_submission(
    submission_id: int, force: bool = False, session: AsyncSession = Depends(get_session)
) -> None:
    submission = await get_submission_or_404(session, submission_id, loaded=False)
    guard_not_in_progress([submission], force)
    await delete_submissions(session, [submission])


async def _get_editable_answer(session: AsyncSession, submission_id: int, question_id: str) -> tuple[Submission, int]:
    """Shared lookup/guards for the manual-grade endpoints below. Returns the
    submission plus the index of the answer."""
    submission = await get_submission_or_404(session, submission_id)
    if submission.status in _IN_PROGRESS_STATUSES:
        # The worker holds its own copy of `answers` and writes the whole
        # list after every question -- an edit made now would just be
        # overwritten by its next commit.
        raise HTTPException(status_code=409, detail="Submission is still being graded -- wait for it to finish")
    for i, answer in enumerate(submission.answers):
        if answer.question_id == question_id:
            return submission, i
    raise HTTPException(status_code=404, detail=f"No answer for question {question_id!r} in this submission")


@router.put("/{submission_id}/answers/{question_id}/grade", response_model=SubmissionOut)
async def set_manual_grade(
    submission_id: int, question_id: str, payload: Grade, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Manually override one answer's grade level + feedback. Stored as
    `manual_grade`, next to (not instead of) the LLM's own `grade` -- see
    `AnsweredQuestion`'s docstring -- and survives a re-grade."""
    submission, i = await _get_editable_answer(session, submission_id, question_id)
    level_ids = [level.id for level in submission.rubric.grading_scale]
    if payload.level not in level_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown level {payload.level!r} -- this rubric's levels are: {', '.join(level_ids)}",
        )

    now = datetime.now(UTC)
    submission.answers[i].manual_grade = payload
    submission.answers[i].manual_graded_at = now
    flag_modified(submission, "answers")
    submission.updated_at = now
    await session.commit()
    return submission


@router.delete("/{submission_id}/answers/{question_id}/grade", response_model=SubmissionOut)
async def clear_manual_grade(
    submission_id: int, question_id: str, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Drop a manual override, reverting that answer to the LLM's grade."""
    submission, i = await _get_editable_answer(session, submission_id, question_id)
    submission.answers[i].manual_grade = None
    submission.answers[i].manual_graded_at = None
    flag_modified(submission, "answers")
    submission.updated_at = datetime.now(UTC)
    await session.commit()
    return submission


@router.get("/{submission_id}/file")
async def download_submission_file(
    submission_id: int, inline: bool = False, session: AsyncSession = Depends(get_session)
) -> Response:
    """`inline=true` lets the browser display it (the segmentation editor
    shows the PDF in an iframe) instead of downloading it."""
    submission = await get_submission_or_404(session, submission_id)

    extension, content_type = FORMAT_FILE_INFO[submission.rubric.format]
    file_bytes = storage.download_bytes(submission.file_object_key)
    # Never breaks on a null student (batch-created or labelled) -- see
    # Submission.file_identifier's fallback chain.
    filename = f"{submission.file_identifier}_{submission.rubric.slug}{extension}"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"'},
    )


@router.get("/{submission_id}/original")
async def download_original_upload(submission_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """The file exactly as uploaded, for a submission that was converted
    before storing (e.g. a `.docx` against a generic-document rubric --
    `/file` serves the converted PDF). `404` when there's no separate
    original: the upload was stored as-is (use `/file`)."""
    submission = await get_submission_or_404(session, submission_id, loaded=False)
    if submission.original_object_key is None:
        raise HTTPException(
            status_code=404,
            detail="No separate original upload stored for this submission (it was stored as uploaded) "
            "-- the stored file is at /file",
        )
    extension = "." + submission.original_object_key.rsplit(".", 1)[-1]
    filename = submission.original_filename or f"{submission.id}{extension}"
    return Response(
        content=storage.download_bytes(submission.original_object_key),
        media_type=UPLOAD_CONTENT_TYPES.get(extension, "application/octet-stream"),
        headers={"Content-Disposition": content_disposition("attachment", filename)},
    )


@router.get("/{submission_id}/answers/{question_id}/images/{index}")
async def get_answer_image(
    submission_id: int, question_id: str, index: int, session: AsyncSession = Depends(get_session)
) -> Response:
    """The `index`-th image attached to this answer's grading call. Images
    aren't stored anywhere -- this re-runs extraction on the stored file,
    which is cheap for the formats that have images (notebooks)."""
    submission = await get_submission_or_404(session, submission_id)
    rubric = submission.rubric
    question = next((q for q in rubric.questions if q.id == question_id), None)
    if question is None:
        raise HTTPException(status_code=404, detail=f"Question {question_id!r} not found in this rubric")

    images = extract_submission_file(submission).images.get(question.answer_key, [])
    if not 0 <= index < len(images):
        raise HTTPException(status_code=404, detail="Image not found")
    image = images[index]
    return Response(content=base64.b64decode(image.data), media_type=image.mime_type)


@router.get("/{submission_id}/feedback.html")
async def download_submission_feedback(submission_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """Standalone HTML export of this submission's graded feedback -- see
    `feedback_export.render_feedback_html`'s docstring for why it's a single
    self-contained file rather than e.g. rendering the same page the SPA
    does."""
    submission = await get_submission_or_404(session, submission_id)

    return Response(
        content=render_feedback_html(
            submission, load_answer_images(submission), course=await course_name(session, submission.rubric)
        ),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{feedback_filename(submission)}"'},
    )


class BulkRegradeRequest(BaseModel):
    submission_ids: list[int]
    question_id: str | None = None


class BulkRegradeResult(BaseModel):
    regraded: int


@router.post("/regrade", response_model=BulkRegradeResult, status_code=202)
async def regrade_submissions(
    body: BulkRegradeRequest, model_id: int | None = MODEL_ID_QUERY, session: AsyncSession = Depends(get_session)
) -> BulkRegradeResult:
    """Re-grade an explicit set of submissions (e.g. whatever a filtered list
    shows). With `question_id`, only that question is re-graded; `422` if it
    isn't in every involved rubric."""
    llm_model = await require_llm_model(session, model_id)
    submissions = list(
        await session.scalars(
            select(Submission).options(selectinload(Submission.rubric)).where(Submission.id.in_(body.submission_ids))
        )
    )
    if body.question_id is not None and any(
        body.question_id not in {q.id for q in s.rubric.questions} for s in submissions
    ):
        raise HTTPException(status_code=422, detail=f"Question {body.question_id!r} is not in every submission's rubric")
    await enqueue_regrades(session, submissions, llm_model.id, body.question_id)
    return BulkRegradeResult(regraded=len(submissions))


@router.post("/{submission_id}/regrade", response_model=SubmissionOut, status_code=202)
async def regrade_submission(
    submission_id: int, model_id: int | None = MODEL_ID_QUERY, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Force a submission back through extraction+grading from scratch.

    `_grade_submission` always re-downloads the raw file, re-extracts answers
    (using whichever extractor `rubric.format` selects), and rebuilds
    `answers` from the rubric's current questions, so this discards any
    previous LLM grades/feedback -- exactly what "force re-grade" should do
    (e.g. after fixing a rubric or the uploaded file, or retrying a `failed`
    run). TA manual overrides (`manual_grade`) are the exception: they're
    carried over by question id (see worker/tasks.py's `_rebuild_answers`).
    """
    llm_model = await require_llm_model(session, model_id)
    submission = await get_submission_or_404(session, submission_id)
    await enqueue_regrades(session, [submission], llm_model.id)
    return submission


# --- Generic-document segmentation (AGENTS.md "Generic-document submissions") ---


class DocumentImageView(BaseModel):
    index: int
    name: str
    line: int
    page: int


class DocumentView(BaseModel):
    """A converted generic document, for the segmentation editor."""

    markdown: str
    line_count: int
    page_start_lines: list[int]
    images: list[DocumentImageView]


def _require_document(submission: Submission):
    if submission.document is None:
        raise HTTPException(status_code=409, detail="This submission has no converted document (yet)")
    return submission.document


@router.get("/{submission_id}/document", response_model=DocumentView)
async def get_submission_document(submission_id: int, session: AsyncSession = Depends(get_session)) -> DocumentView:
    submission = await get_submission_or_404(session, submission_id, loaded=False)
    document = _require_document(submission)
    return DocumentView(
        markdown=storage.download_bytes(document.markdown_object_key).decode("utf-8"),
        line_count=document.line_count,
        page_start_lines=document.page_start_lines,
        images=[
            DocumentImageView(index=i, name=img.name, line=img.line, page=img.page)
            for i, img in enumerate(document.images)
        ],
    )


@router.get("/{submission_id}/document/images/{index}")
async def get_submission_document_image(
    submission_id: int, index: int, session: AsyncSession = Depends(get_session)
) -> Response:
    submission = await get_submission_or_404(session, submission_id, loaded=False)
    images = _require_document(submission).images
    if not 0 <= index < len(images):
        raise HTTPException(status_code=404, detail="Image not found")
    image = images[index]
    return Response(content=storage.download_bytes(image.object_key), media_type=image.mime_type)


class SegmentsUpdate(BaseModel):
    segments: list[AnswerSegment]
    # Also enqueue grading (every rubric question must have a segment).
    grade: bool = False


async def _get_segmentable(session: AsyncSession, submission_id: int) -> Submission:
    submission = await get_submission_or_404(session, submission_id)
    if submission.rubric.format not in SEGMENTED_FORMATS:
        raise HTTPException(
            status_code=422, detail=f"Rubric format {submission.rubric.format.value!r} does not use segmentation"
        )
    if submission.status in _IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=409, detail="Submission is still being processed -- wait for it to finish")
    _require_document(submission)
    return submission


@router.put("/{submission_id}/segments", response_model=SubmissionOut)
async def set_submission_segments(
    submission_id: int,
    body: SegmentsUpdate,
    model_id: int | None = MODEL_ID_QUERY,
    session: AsyncSession = Depends(get_session),
) -> Submission:
    """Save a professor's segmentation. Without `grade`, the submission is
    left `awaiting_segmentation` (any existing grades are now stale); with
    it, grading is enqueued -- the only way a generic document gets graded.
    `422` on invalid segments, or on `grade` with a question still missing
    its segment."""
    submission = await _get_segmentable(session, submission_id)
    questions = submission.rubric.questions
    try:
        validate_segments(body.segments, submission.document.line_count, [q.id for q in questions])
    except SegmentationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.grade and (missing := missing_questions(body.segments, [q.id for q in questions])):
        raise HTTPException(status_code=422, detail="No segment decided yet for question(s): " + ", ".join(missing))
    llm_model = await require_llm_model(session, model_id) if body.grade else None

    now = datetime.now(UTC)
    submission.segments = body.segments
    submission.segmentation_source = SegmentationSource.MANUAL
    submission.segmented_at = now
    submission.status = SubmissionStatus.AWAITING_SEGMENTATION
    submission.error = None
    submission.updated_at = now
    if llm_model is not None:
        await enqueue_regrades(session, [submission], llm_model.id)
    else:
        await session.commit()
    return submission


@router.post("/{submission_id}/segments/suggest", response_model=SubmissionOut, status_code=202)
async def suggest_submission_segments(
    submission_id: int, model_id: int | None = MODEL_ID_QUERY, session: AsyncSession = Depends(get_session)
) -> Submission:
    """Ask the LLM for a segmentation suggestion (replaces the current
    segments once it's done). Async: the submission goes `segmenting`, then
    back to `awaiting_segmentation`."""
    llm_model = await require_llm_model(session, model_id)
    submission = await _get_segmentable(session, submission_id)
    await enqueue_segment_suggestions(session, [submission], llm_model.id)
    return submission


async def enqueue_segment_suggestions(session: AsyncSession, submissions: list[Submission], model_id: int) -> None:
    for submission in submissions:
        submission.status = SubmissionStatus.SEGMENTING
        submission.error = None
    await session.commit()
    for submission in submissions:
        suggest_segments_task.delay(submission.id, model_id=model_id)
