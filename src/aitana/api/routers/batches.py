"""Batch upload of submissions from a zip export (e.g. Atenea's per-assignment
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

from beanie import PydanticObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ... import storage
from ...documents import Batch, BatchType, Rubric, Student, Submission, SubmissionStatus
from ...grading.extraction import FORMAT_FILE_INFO
from ...worker.tasks import grade_submission
from .submissions import _resolve_edition

router = APIRouter(prefix="/batches", tags=["batches"])

# Batch.rubric links to Course/Edition itself, so fetching it needs the same
# depth cap as Submission.rubric (see submissions.py's _SHALLOW_LINKS and
# AGENTS.md's nested-$lookup-under-mongomock note) -- callers here only ever
# use rubric.slug/title, never rubric.course/rubric.edition.
_BATCH_SHALLOW_LINKS = {"rubric": 1}


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


def _parse_atenea_zip(zf: zipfile.ZipFile) -> list[_BatchItem]:
    """Atenea's assignment-submission export: one top-level folder per
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
    BatchType.ATENEA: _parse_atenea_zip,
}

# Strips Atenea's fixed `_<internal_id>_assignsubmission_file` suffix off a
# folder name, leaving just the "<surname(s)> <name>" part to match against
# Student.name.
_ATENEA_FOLDER_NAME_SUFFIX_RE = re.compile(r"_\d+_assignsubmission_file$", re.IGNORECASE)


def _name_tokens(text: str) -> frozenset[str]:
    """Lowercase, accent-folded word tokens, order-independent -- an Atenea
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
    folder_tokens = _name_tokens(_ATENEA_FOLDER_NAME_SUFFIX_RE.sub("", folder_name))
    if not folder_tokens:
        return None
    matches = [student for student in students if _name_tokens(student.name) == folder_tokens]
    return matches[0] if len(matches) == 1 else None


class BatchUploadResult(BaseModel):
    batch: Batch
    created: int
    submissions: list[Submission]


@router.post("", response_model=BatchUploadResult, status_code=202)
async def create_batch(
    file: UploadFile = File(...),
    rubric_id: PydanticObjectId = Form(...),
    edition_id: PydanticObjectId | None = Form(None),
    type: BatchType = Form(...),
) -> BatchUploadResult:
    rubric = await Rubric.get(rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="Rubric not found")
    edition = await _resolve_edition(rubric, edition_id)

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
    mismatched = sorted(item.folder_name for item in items if not item.filename.lower().endswith(extension))
    if mismatched:
        raise HTTPException(
            status_code=422,
            detail=f"This rubric expects {extension} files ({rubric.format.value}) -- mismatched folder(s): "
            + ", ".join(mismatched),
        )

    batch = Batch(rubric=rubric, edition=edition, type=type, original_filename=file.filename, item_count=len(items))
    await batch.insert()

    # One query for the whole roster, matched per item in memory -- cheaper
    # than a query per item, and `_match_student` needs the full list anyway
    # to detect an ambiguous (shared-name) match.
    students = await Student.find_all().to_list()

    submissions: list[Submission] = []
    for item in items:
        submission = Submission(
            rubric=rubric,
            edition=edition,
            student=_match_student(item.folder_name, students),
            batch=batch,
            batch_internal_id=item.folder_name,
            file_object_key="",
        )
        await submission.insert()

        key = storage.batch_object_key(rubric.slug, str(submission.id), extension)
        storage.upload_file(key, item.data, content_type)
        submission.file_object_key = key
        await submission.save()

        grade_submission.delay(str(submission.id))
        submissions.append(submission)

    return BatchUploadResult(batch=batch, created=len(submissions), submissions=submissions)


@router.get("", response_model=list[Batch])
async def list_batches(
    rubric_id: PydanticObjectId | None = None,
    edition_id: PydanticObjectId | None = None,
) -> list[Batch]:
    filters = []
    if rubric_id is not None:
        filters.append(Batch.rubric.id == rubric_id)
    if edition_id is not None:
        filters.append(Batch.edition.id == edition_id)
    return await Batch.find(*filters, fetch_links=True, nesting_depths_per_field=_BATCH_SHALLOW_LINKS).to_list()


@router.get("/{batch_id}", response_model=Batch)
async def get_batch(batch_id: PydanticObjectId) -> Batch:
    batch = await Batch.get(batch_id, fetch_links=True, nesting_depths_per_field=_BATCH_SHALLOW_LINKS)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


class BatchRegradeResult(BaseModel):
    regraded: int


@router.post("/{batch_id}/regrade", response_model=BatchRegradeResult, status_code=202)
async def regrade_batch(batch_id: PydanticObjectId) -> BatchRegradeResult:
    """Force every submission in this batch back through extraction+grading
    from scratch -- the bulk version of `POST /submissions/{id}/regrade`
    (same per-submission reset: status back to `pending`, `error` cleared,
    re-enqueued), for when a whole batch needs re-running (e.g. after fixing
    the rubric, or a bad LLM provider config affected the whole run)."""
    batch = await Batch.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    submissions = await Submission.find(Submission.batch.id == batch_id).to_list()
    for submission in submissions:
        submission.status = SubmissionStatus.PENDING
        submission.error = None
        await submission.save()
        grade_submission.delay(str(submission.id))

    return BatchRegradeResult(regraded=len(submissions))
