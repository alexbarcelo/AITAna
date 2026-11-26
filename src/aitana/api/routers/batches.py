"""Batch upload of submissions from a zip export (e.g. Moodle's per-assignment
"download all submissions" zip): one subfolder per student, all against the
same rubric.

Adding a new zip layout means writing one `(zipfile.ZipFile) ->
list[_BatchItem]` parser and registering it in `_BATCH_PARSERS` -- same
registry shape as `StudentImportFormat`/`_IMPORT_PARSERS` in
`api/routers/students.py`.
"""

import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import storage
from ...db import BATCH_LOADS, SUBMISSION_LOADS, Batch, BatchType, Student, Submission, SubmissionStatus
from ...db.session import get_session
from ...grading.extraction import FORMAT_FILE_INFO
from ..feedback_export import course_name, load_answer_images, render_feedback_html
from ..schemas import BatchOut, SubmissionOut
from .llm import MODEL_ID_QUERY, require_llm_model
from .submissions import (
    delete_submissions,
    enqueue_initial_processing,
    enqueue_regrades,
    enqueue_segment_suggestions,
    expected_extensions_text,
    get_rubric_or_404,
    guard_not_in_progress,
    original_upload_name,
    resolve_edition,
    store_original_upload,
    to_stored_format,
    upload_extension,
    upload_model,
)

router = APIRouter(prefix="/batches", tags=["batches"])


async def _get_batch(session: AsyncSession, batch_id: int, *, loaded: bool = False) -> Batch:
    batch = await session.get(Batch, batch_id, options=BATCH_LOADS if loaded else ())
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


async def _batch_submissions(
    session: AsyncSession, batch_id: int, *conditions, loaded: bool = False
) -> list[Submission]:
    query = select(Submission).where(Submission.batch_id == batch_id, *conditions).order_by(Submission.id)
    if loaded:
        query = query.options(*SUBMISSION_LOADS)
    return list(await session.scalars(query))


@dataclass
class _BatchItem:
    folder_name: str  # kept verbatim -- becomes Submission.batch_internal_id
    filename: str
    data: bytes


def _is_junk(path: PurePosixPath) -> bool:
    """`__MACOSX/` and dotfiles are export artifacts, never a real student
    folder/file -- and anything absolute or with a `..` segment would escape
    the extraction root (zip-slip)."""
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return True
    return not path.parts or path.parts[0] == "__MACOSX" or any(part.startswith(".") for part in path.parts)


def _parse_moodle_zip(zf: zipfile.ZipFile) -> list[_BatchItem]:
    """Moodle's assignment-submission export: one top-level folder per
    student submission (named `<surname(s)> <name>_<internal_id>_
    assignsubmission_file`), each containing exactly one file. The folder
    name is kept verbatim, not parsed into name/internal-id parts -- see
    `Submission.batch_internal_id`'s docstring for why.

    A folder's presence is read off its own directory entry, not merely
    inferred from file paths -- a folder with zero files (e.g. a student who
    never submitted) still shows up in the zip as an empty directory entry,
    and is reported as an offending folder alongside one holding more than
    one file, rather than silently dropped.
    """
    folder_names: set[str] = set()
    by_folder: dict[str, list[zipfile.ZipInfo]] = defaultdict(list)
    stray: list[str] = []

    for info in zf.infolist():
        path = PurePosixPath(info.filename)
        if _is_junk(path):
            continue
        if info.is_dir():
            if len(path.parts) == 1:
                folder_names.add(path.parts[0])
            continue
        if len(path.parts) < 2:
            stray.append(info.filename)
            continue
        folder_names.add(path.parts[0])
        by_folder[path.parts[0]].append(info)

    if stray:
        raise ValueError(
            "Every file must be inside a per-student folder -- found file(s) at the zip root: "
            + ", ".join(sorted(stray))
        )
    if not folder_names:
        raise ValueError("Zip file has no per-student folders")

    bad = {folder: len(by_folder[folder]) for folder in folder_names if len(by_folder[folder]) != 1}
    if bad:
        detail = ", ".join(f"{folder!r} ({count} files)" for folder, count in sorted(bad.items()))
        raise ValueError(f"Each folder must contain exactly one file -- offending folder(s): {detail}")

    return [
        _BatchItem(folder_name=folder, filename=PurePosixPath(infos[0].filename).name, data=zf.read(infos[0]))
        for folder, infos in by_folder.items()
    ]


_BATCH_PARSERS: dict[BatchType, Callable[[zipfile.ZipFile], list[_BatchItem]]] = {
    BatchType.MOODLE: _parse_moodle_zip,
}

# Strips Moodle's fixed `_<internal_id>_assignsubmission_file` suffix off a
# folder name, leaving just the "<surname(s)> <name>" part to match against
# Student.name.
_MOODLE_FOLDER_NAME_SUFFIX_RE = re.compile(r"_\d+_assignsubmission_file$", re.IGNORECASE)


def _name_tokens(text: str) -> frozenset[str]:
    """Lowercase, accent-folded word tokens, order-independent -- a Moodle
    folder name is "<surname(s)> <first name>" while `Student.name` is
    "<first name> <surname(s)>" (see `_parse_atenea_csv` in
    `api/routers/students.py`), so token-*set* equality is what actually
    matches these, not a direct string comparison."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return frozenset(token for token in re.split(r"[^a-zA-Z]+", folded.lower()) if token)


def _match_student(folder_name: str, students: list[Student]) -> Student | None:
    """Best-effort auto-match of a batch item to a roster student by name.
    Only commits to a match when exactly one student's name has the
    identical token set -- an empty or ambiguous result (no student, or more
    than one sharing a name) is left for a human to resolve via `PUT
    /submissions/{id}/student` instead of risking a wrong guess."""
    folder_tokens = _name_tokens(_MOODLE_FOLDER_NAME_SUFFIX_RE.sub("", folder_name))
    if not folder_tokens:
        return None
    matches = [student for student in students if _name_tokens(student.name) == folder_tokens]
    return matches[0] if len(matches) == 1 else None


def _folder_label(folder_name: str) -> str:
    """Label for a batch item when students aren't matched: the folder name without Moodle's fixed
    suffix, or the whole folder name if stripping leaves nothing."""
    return _MOODLE_FOLDER_NAME_SUFFIX_RE.sub("", folder_name).strip() or folder_name


class BatchUploadResult(BaseModel):
    batch: BatchOut
    created: int
    submissions: list[SubmissionOut]


@router.post("", response_model=BatchUploadResult, status_code=202)
async def create_batch(
    file: UploadFile = File(...),
    rubric_id: int = Form(...),
    edition_id: int | None = Form(None),
    type: BatchType = Form(...),
    llm_segmentation: bool = Form(True),
    match_students: bool = Form(True),
    model_id: int | None = MODEL_ID_QUERY,
    session: AsyncSession = Depends(get_session),
) -> BatchUploadResult:
    """`llm_segmentation`: see `create_submission` (generic documents only).

    `match_students=false` makes every item a labelled submission (e.g.
    group projects): no roster auto-match, and `Submission.label` is the
    folder name minus Moodle's `_<id>_assignsubmission_file` suffix (the
    group name, for a group assignment). `batch_internal_id` is still kept
    verbatim either way, for the feedback zip."""
    rubric = await get_rubric_or_404(session, rubric_id)
    resolved_model_id = await upload_model(session, rubric, model_id, llm_segmentation)
    edition = await resolve_edition(session, rubric, edition_id)

    raw = await file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            items = _BATCH_PARSERS[type](zf)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail=f"Not a valid zip file: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Same early-and-obvious check create_submission does for a single file
    # -- catches an obviously wrong zip before grading starts on every item.
    extension, content_type = FORMAT_FILE_INFO[rubric.format]
    item_extensions = [upload_extension(rubric.format, item.filename) for item in items]
    mismatched = sorted(item.folder_name for item, ext in zip(items, item_extensions, strict=True) if ext is None)
    if mismatched:
        raise HTTPException(
            status_code=422,
            detail=f"This rubric expects {expected_extensions_text(rubric.format)} files ({rubric.format.value}) "
            "-- mismatched folder(s): " + ", ".join(mismatched),
        )
    # Convert every non-native file (e.g. .docx -> PDF) before writing
    # anything, so one unreadable file fails the whole upload cleanly.
    stored_data = [
        await to_stored_format(rubric.format, ext, item.data, f"{item.folder_name}/{item.filename}")
        for item, ext in zip(items, item_extensions, strict=True)
    ]

    batch = Batch(rubric=rubric, edition=edition, type=type, original_filename=file.filename, item_count=len(items))
    session.add(batch)

    # One query for the whole roster, matched per item in memory -- cheaper
    # than a query per item, and `_match_student` needs the full list anyway
    # to detect an ambiguous (shared-name) match.
    students = list(await session.scalars(select(Student))) if match_students else []

    submissions: list[Submission] = []
    for item in items:
        submission = Submission(
            rubric=rubric,
            edition=edition,
            student=_match_student(item.folder_name, students) if match_students else None,
            label=None if match_students else _folder_label(item.folder_name),
            batch=batch,
            batch_internal_id=item.folder_name,
            original_filename=original_upload_name(item.filename),
        )
        session.add(submission)
        submissions.append(submission)
    # Ids are part of the storage keys: flush to get them, store every file,
    # then commit -- a storage failure leaves no rows behind.
    await session.flush()

    for submission, item, ext, data in zip(submissions, items, item_extensions, stored_data, strict=True):
        key = storage.batch_object_key(rubric.slug, str(submission.id), extension)
        storage.upload_file(key, data, content_type)
        submission.file_object_key = key
        store_original_upload(submission, rubric.format, item.data, ext)
    await session.commit()

    for submission in submissions:
        enqueue_initial_processing(submission, rubric, resolved_model_id, llm_segmentation)

    return BatchUploadResult(
        batch=BatchOut.model_validate(batch),
        created=len(submissions),
        submissions=[SubmissionOut.model_validate(s) for s in submissions],
    )


@router.get("", response_model=list[BatchOut])
async def list_batches(
    rubric_id: int | None = None,
    edition_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Batch]:
    query = select(Batch).options(*BATCH_LOADS).order_by(Batch.id)
    if rubric_id is not None:
        query = query.where(Batch.rubric_id == rubric_id)
    if edition_id is not None:
        query = query.where(Batch.edition_id == edition_id)
    return list(await session.scalars(query))


@router.get("/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: int, session: AsyncSession = Depends(get_session)) -> Batch:
    return await _get_batch(session, batch_id, loaded=True)


@router.get("/{batch_id}/feedback.zip")
async def download_batch_feedback(batch_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """Zip of one standalone feedback HTML per submission in this batch,
    each inside a folder named after `Submission.batch_internal_id` -- the
    exact per-student folder name from the original upload (see its
    docstring). Re-uploading this zip as-is into the originating LMS (e.g.
    Moodle's "upload multiple feedback files in a zip" for an
    assignment) then lines each file back up with the right student, the
    same way the LMS's own per-submission export named these folders in the
    first place."""
    batch = await _get_batch(session, batch_id, loaded=True)
    submissions = await _batch_submissions(session, batch_id, loaded=True)

    # One rubric for the whole batch, so one course lookup.
    course = await course_name(session, batch.rubric)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for submission in submissions:
            folder = submission.batch_internal_id or str(submission.id)
            html_out = render_feedback_html(submission, load_answer_images(submission), course=course)
            zf.writestr(f"{folder}/feedback.html", html_out)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{batch.rubric.slug}_feedback.zip"'},
    )


@router.delete("/{batch_id}", status_code=204)
async def delete_batch(batch_id: int, force: bool = False, session: AsyncSession = Depends(get_session)) -> None:
    """Delete a batch together with every submission it created (and their
    grading traces and stored files) -- a batch is just the record of one
    zip upload, so its submissions go with it. Same in-progress guard as
    `DELETE /submissions/{id}`, applied to the whole batch up front: either
    everything is deleted or nothing is."""
    batch = await _get_batch(session, batch_id)
    submissions = await _batch_submissions(session, batch_id)
    guard_not_in_progress(submissions, force)
    await delete_submissions(session, submissions)
    await session.delete(batch)
    await session.commit()


class BatchRegradeResult(BaseModel):
    regraded: int


@router.post("/{batch_id}/regrade", response_model=BatchRegradeResult, status_code=202)
async def regrade_batch(
    batch_id: int,
    question_id: str | None = None,
    model_id: int | None = MODEL_ID_QUERY,
    session: AsyncSession = Depends(get_session),
) -> BatchRegradeResult:
    """Force every submission in this batch back through extraction+grading
    from scratch -- the bulk version of `POST /submissions/{id}/regrade`
    (same per-submission reset: status back to `pending`, `error` cleared,
    re-enqueued), for when a whole batch needs re-running (e.g. after fixing
    the rubric, or a bad LLM provider config affected the whole run).

    With `question_id`, only that question is re-graded (one LLM call per
    submission); every other answer keeps its current grade."""
    llm_model = await require_llm_model(session, model_id)
    batch = await _get_batch(session, batch_id, loaded=True)
    if question_id is not None and question_id not in {q.id for q in batch.rubric.questions}:
        raise HTTPException(status_code=422, detail=f"Question {question_id!r} is not in this batch's rubric")

    submissions = await _batch_submissions(session, batch_id)
    await enqueue_regrades(session, submissions, llm_model.id, question_id)
    return BatchRegradeResult(regraded=len(submissions))


class BatchSegmentSuggestResult(BaseModel):
    suggested: int


@router.post("/{batch_id}/segments/suggest", response_model=BatchSegmentSuggestResult, status_code=202)
async def suggest_batch_segments(
    batch_id: int, model_id: int | None = MODEL_ID_QUERY, session: AsyncSession = Depends(get_session)
) -> BatchSegmentSuggestResult:
    """LLM segmentation suggestion for every generic document in this batch
    that is converted, waiting for segmentation, and has no segments yet --
    e.g. a batch uploaded without LLM-assisted segmentation. Never touches
    a submission someone already segmented."""
    llm_model = await require_llm_model(session, model_id)
    await _get_batch(session, batch_id)
    submissions = await _batch_submissions(
        session, batch_id, Submission.status == SubmissionStatus.AWAITING_SEGMENTATION
    )
    pending = [s for s in submissions if s.document is not None and s.segments is None]
    await enqueue_segment_suggestions(session, pending, llm_model.id)
    return BatchSegmentSuggestResult(suggested=len(pending))
