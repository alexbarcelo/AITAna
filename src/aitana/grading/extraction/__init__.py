"""Pluggable per-format answer extraction.

Every extractor has the same shape: `(file_path: Path) -> ExtractionResult`
(`grading/models.py`): `answers` maps a `Question.answer_key` to the
student's raw extracted text, `untagged_cells` reports any answer the
extractor had to infer heuristically (empty for formats with no heuristics).
`extract_answers(format, path)` below dispatches to the one registered for
`format`. Adding a new format means writing one such function in a sibling
module and registering it in `_EXTRACTORS` (and `FORMAT_FILE_INFO`, for the
extension/content-type it's stored/served with) -- nothing in
`worker/tasks.py` or `api/routers/submissions.py` needs to change.
"""

from collections.abc import Callable
from pathlib import Path

from ..models import ExtractionResult, SubmissionFormat
from . import notebook, pdf_form

# `generic_document` is deliberately absent from `_EXTRACTORS`: its answers come
# from the Markdown converted once at upload plus the professor's segments
# (both stored), not from re-reading the raw file -- see generic_document.py and
# worker/tasks.py's `extract_submission_file`.

_EXTRACTORS: dict[SubmissionFormat, Callable[[Path], ExtractionResult]] = {
    SubmissionFormat.PDF_FORM: pdf_form.extract_answers,
    SubmissionFormat.NOTEBOOK: notebook.extract_answers,
}

# (extension, content-type) each format is stored in MinIO / served back as --
# shared by storage.object_key() (upload) and the download endpoint
# (Content-Type + filename) so both stay in lockstep with the extractors above.
FORMAT_FILE_INFO: dict[SubmissionFormat, tuple[str, str]] = {
    SubmissionFormat.PDF_FORM: (".pdf", "application/pdf"),
    SubmissionFormat.NOTEBOOK: (".ipynb", "application/x-ipynb+json"),
    SubmissionFormat.GENERIC_DOCUMENT: (".pdf", "application/pdf"),
}

# Other extensions a format accepts at upload, converted to its
# FORMAT_FILE_INFO extension before storing (generic_document.document_to_pdf) --
# so storage, extraction and download only ever see the stored format.
UPLOAD_CONVERSIONS: dict[SubmissionFormat, frozenset[str]] = {
    SubmissionFormat.GENERIC_DOCUMENT: frozenset({".docx"}),
}

# Content type each UPLOAD_CONVERSIONS extension is served back as, when the
# original (pre-conversion) upload is downloaded for provenance
# (`GET /submissions/{id}/original`).
UPLOAD_CONTENT_TYPES: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def accepted_upload_extensions(fmt: SubmissionFormat) -> list[str]:
    """Every extension an upload against a `fmt` rubric may have."""
    return [FORMAT_FILE_INFO[fmt][0], *sorted(UPLOAD_CONVERSIONS.get(fmt, ()))]


# Formats that need converting + segmenting before they can be graded (see
# generic_document.py) -- their submissions go through `awaiting_segmentation`.
SEGMENTED_FORMATS: frozenset[SubmissionFormat] = frozenset({SubmissionFormat.GENERIC_DOCUMENT})

# Formats whose extractor fills `ExtractionResult.answers_with_outputs` /
# `.images` -- i.e. where `Question.include_outputs` / `.include_images` mean
# something (enforced by Rubric's validator). Supporting images for another
# format = fill `images` in its extractor and add it here.
OUTPUT_FORMATS: frozenset[SubmissionFormat] = frozenset({SubmissionFormat.NOTEBOOK})
IMAGE_FORMATS: frozenset[SubmissionFormat] = frozenset({SubmissionFormat.NOTEBOOK, SubmissionFormat.GENERIC_DOCUMENT})


def extract_answers(fmt: SubmissionFormat, file_path: Path) -> ExtractionResult:
    return _EXTRACTORS[fmt](file_path)
