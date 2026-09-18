from beanie import Link, PydanticObjectId
from beanie.operators import In
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from ... import storage
from ...documents import Edition, Rubric, Student, Submission, SubmissionStatus
from ...grading.extraction import FORMAT_FILE_INFO
from ...worker.tasks import grade_submission
from ..feedback_export import feedback_filename, render_feedback_html

router = APIRouter(prefix="/submissions", tags=["submissions"])

# Submission views only ever use rubric.slug/title/questions, never its
# nested `course`/`edition` links -- capping fetch_links depth on `rubric`
# avoids resolving that extra hop (a real, if small, saving) and avoids the
# nested-pipeline $lookup mongomock can't run (see AGENTS.md sharp edge #6).
# `batch` needs the same cap for the same reason (Batch.rubric/.edition are
# themselves Links). `edition` needs no cap of its own: Edition is global and
# carries no Link fields at all anymore (see documents/edition.py), so
# resolving it fully never produces a nested pipeline in the first place.
_SHALLOW_LINKS = {"rubric": 1, "batch": 1}


async def _resolve_edition(rubric: Rubric, edition_id: PydanticObjectId | None) -> Edition:
    """A submission always has a definite edition, even for a rubric that
    doesn't fix one (e.g. a lab reused across years): use the rubric's own
    edition if it has one, otherwise the uploader must supply one."""
    if rubric.edition is not None:
        # `rubric` here is always fetched without fetch_links (see
        # create_submission), so a set `edition` is an unresolved Link --
        # `.fetch()` does a plain get-by-id, no aggregation needed.
        edition = await rubric.edition.fetch()
        if isinstance(edition, Link):  # dangling reference -- shouldn't happen, but don't silently misassign
            raise HTTPException(status_code=404, detail="This rubric's edition no longer exists")
        return edition

    if edition_id is None:
        raise HTTPException(
            status_code=422,
            detail="This rubric has no fixed edition -- edition_id is required",
        )
    edition = await Edition.get(edition_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return edition


@router.post("", response_model=Submission, status_code=202)
async def create_submission(
    file: UploadFile = File(...),
    student_id: PydanticObjectId = Form(...),
    rubric_id: PydanticObjectId = Form(...),
    edition_id: PydanticObjectId | None = Form(None),
) -> Submission:
    student = await Student.get(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")
    edition = await _resolve_edition(rubric, edition_id)

    # The rubric, not the uploader, decides what format its submissions are
    # in (see Rubric.format's docstring) -- this only catches an obvious
    # mismatch (wrong file picked in the form) early, as a 422 rather than a
    # confusing extraction failure once grading starts.
    extension, content_type = FORMAT_FILE_INFO[rubric.format]
    filename = file.filename or ""
    if filename and not filename.lower().endswith(extension):
        raise HTTPException(
            status_code=422,
            detail=f"This rubric expects a {extension} file ({rubric.format.value}), got {filename!r}",
        )

    submission = Submission(student=student, rubric=rubric, edition=edition, file_object_key="")
    await submission.insert()

    key = storage.object_key(rubric.slug, student.student_id, str(submission.id), extension)
    storage.upload_file(key, await file.read(), content_type)
    submission.file_object_key = key
    await submission.save()

    grade_submission.delay(str(submission.id))
    return submission


@router.get("", response_model=list[Submission])
async def list_submissions(
    student_id: PydanticObjectId | None = None,
    rubric_id: PydanticObjectId | None = None,
    course_id: PydanticObjectId | None = None,
    edition_id: PydanticObjectId | None = None,
    batch_id: PydanticObjectId | None = None,
    status: SubmissionStatus | None = None,
) -> list[Submission]:
    filters = []
    if student_id is not None:
        filters.append(Submission.student.id == student_id)
    if rubric_id is not None:
        filters.append(Submission.rubric.id == rubric_id)
    if batch_id is not None:
        filters.append(Submission.batch.id == batch_id)
    if status is not None:
        filters.append(Submission.status == status)
    if course_id is not None:
        # A rubric's course is the authority for "course" filtering -- find
        # this course's rubrics first, then submissions against any of them
        # (two-step In() pattern, verified against real MongoDB; see AGENTS.md).
        rubric_ids = [r.id for r in await Rubric.find(Rubric.course.id == course_id).to_list()]
        filters.append(In(Submission.rubric.id, rubric_ids))
    if edition_id is not None:
        # Direct, unlike course_id above: every Submission carries its own
        # edition (see Submission.edition's docstring), so this doesn't need
        # to go via Student enrollment at all.
        filters.append(Submission.edition.id == edition_id)
    return await Submission.find(*filters, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS).to_list()


@router.get("/{submission_id}", response_model=Submission)
async def get_submission(submission_id: PydanticObjectId) -> Submission:
    submission = await Submission.get(submission_id, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


class SetSubmissionStudent(BaseModel):
    student_id: PydanticObjectId


@router.put("/{submission_id}/student", response_model=Submission)
async def set_submission_student(submission_id: PydanticObjectId, payload: SetSubmissionStudent) -> Submission:
    """Manually set (or correct) which student a submission belongs to --
    mainly for a batch-created submission whose folder name didn't
    auto-match anyone, or matched the wrong person (see
    api/routers/batches.py's `_match_student`), but works for any
    submission."""
    submission = await Submission.get(submission_id, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    student = await Student.get(payload.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    # Assign the just-fetched Student object directly rather than re-fetching
    # the submission afterwards -- the in-memory `student` field already
    # holds the full document, no second round-trip needed to serialize it
    # back out.
    submission.student = student
    await submission.save()
    return submission


@router.get("/{submission_id}/file")
async def download_submission_file(submission_id: PydanticObjectId) -> Response:
    submission = await Submission.get(submission_id, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    extension, content_type = FORMAT_FILE_INFO[submission.rubric.format]
    file_bytes = storage.download_bytes(submission.file_object_key)
    # A batch-created submission has no student yet (see Submission.student's
    # docstring) -- fall back to its batch folder name, or the submission id
    # as a last resort, so this never breaks on a null student.
    identifier = submission.student.student_id if submission.student else (submission.batch_internal_id or str(submission.id))
    filename = f"{identifier}_{submission.rubric.slug}{extension}"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{submission_id}/feedback.html")
async def download_submission_feedback(submission_id: PydanticObjectId) -> Response:
    """Standalone HTML export of this submission's graded feedback -- see
    `feedback_export.render_feedback_html`'s docstring for why it's a single
    self-contained file rather than e.g. rendering the same page the SPA
    does."""
    submission = await Submission.get(submission_id, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    return Response(
        content=render_feedback_html(submission),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{feedback_filename(submission)}"'},
    )


@router.post("/{submission_id}/regrade", response_model=Submission, status_code=202)
async def regrade_submission(submission_id: PydanticObjectId) -> Submission:
    """Force a submission back through extraction+grading from scratch.

    `_grade_submission` always re-downloads the raw file, re-extracts answers
    (using whichever extractor `rubric.format` selects), and rebuilds
    `answers` from the rubric's current questions, so this discards any
    previous grades/feedback -- exactly what "force re-grade" should do (e.g.
    after fixing a rubric or the uploaded file, or retrying a `failed` run).
    """
    submission = await Submission.get(submission_id, fetch_links=True, nesting_depths_per_field=_SHALLOW_LINKS)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    submission.status = SubmissionStatus.PENDING
    submission.error = None
    await submission.save()

    grade_submission.delay(str(submission.id))
    return submission
