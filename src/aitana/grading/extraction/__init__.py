"""Pluggable per-format answer extraction.

Every extractor has the same shape: `(file_path: Path) -> dict[str, str]`,
mapping a `Question.answer_key` to the student's raw extracted text.
`extract_answers(format, path)` below dispatches to the one registered for
`format`. Adding a new format means writing one such function in a sibling
module and registering it in `_EXTRACTORS` (and `FORMAT_FILE_INFO`, for the
extension/content-type it's stored/served with) -- nothing in
`worker/tasks.py` or `api/routers/submissions.py` needs to change.
"""

from collections.abc import Callable
from pathlib import Path

from ..models import SubmissionFormat
from . import notebook, pdf

_EXTRACTORS: dict[SubmissionFormat, Callable[[Path], dict[str, str]]] = {
    SubmissionFormat.PDF: pdf.extract_answers,
    SubmissionFormat.NOTEBOOK: notebook.extract_answers,
}

# (extension, content-type) each format is stored in MinIO / served back as --
# shared by storage.object_key() (upload) and the download endpoint
# (Content-Type + filename) so both stay in lockstep with the extractors above.
FORMAT_FILE_INFO: dict[SubmissionFormat, tuple[str, str]] = {
    SubmissionFormat.PDF: (".pdf", "application/pdf"),
    SubmissionFormat.NOTEBOOK: (".ipynb", "application/x-ipynb+json"),
}


def extract_answers(fmt: SubmissionFormat, file_path: Path) -> dict[str, str]:
    return _EXTRACTORS[fmt](file_path)
