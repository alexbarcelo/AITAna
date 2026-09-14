"""Extracts student answers from a filled-in PDF form.

One of the pluggable extractors dispatched by `extraction/__init__.py`'s
`extract_answers(format, path)` for `SubmissionFormat.PDF` rubrics. Assumes
the lab answer sheet is a PDF with AcroForm text fields named answer1,
answer2, etc. -- one per `\\question` in the lab guide -- matching
`Question.answer_key` (`grading/models.py`).
"""

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_answers(pdf_path: Path) -> dict[str, str]:
    """Return {field_name: text_value} for every text form field in the PDF."""
    logger.debug("Opening PDF %s", pdf_path)
    reader = PdfReader(pdf_path)
    # get_form_text_fields() already filters to "/Tx" (text) fields and reads
    # their current value -- no need to walk the AcroForm tree by hand.
    fields = reader.get_form_text_fields() or {}
    answers = {name: (value or "").strip() for name, value in fields.items()}
    non_blank = sum(1 for v in answers.values() if v)
    logger.info("Extracted %d text field(s) from %s (%d non-blank)", len(answers), pdf_path, non_blank)
    logger.debug("Field names: %s", list(answers.keys()))
    return answers
