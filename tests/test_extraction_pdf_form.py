from pathlib import Path

import pymupdf

from aitana.grading.extraction.pdf_form import extract_answers


def _make_answer_sheet(path: Path, fields: dict[str, str]) -> None:
    """Write a small fillable PDF with one AcroForm text field per entry,
    like a LaTeX lab answer sheet, plus a checkbox (not a text field, so the
    extractor must skip it). Built per test with made-up answers, so the
    suite never depends on a real student's submission."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, (name, value) in enumerate(fields.items()):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = name
        widget.field_value = value
        widget.field_flags = pymupdf.PDF_TX_FIELD_IS_MULTILINE
        widget.rect = pymupdf.Rect(50, 50 + i * 90, 550, 130 + i * 90)
        page.add_widget(widget)
    checkbox = pymupdf.Widget()
    checkbox.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    checkbox.field_name = "agree"
    checkbox.field_value = True
    checkbox.rect = pymupdf.Rect(50, 700, 70, 720)
    page.add_widget(checkbox)
    doc.save(path)
    doc.close()


def test_extract_answers_from_answer_sheet(tmp_path):
    pdf = tmp_path / "answers.pdf"
    _make_answer_sheet(
        pdf,
        {
            "answer1": "  Docker pulled the image and ran it.\nThen it printed a greeting.  ",
            "answer2": "Bind mounts map a host path; volumes are managed by Docker.",
            "answer3": "",
        },
    )

    result = extract_answers(pdf)

    assert result.answers == {
        "answer1": "Docker pulled the image and ran it.\nThen it printed a greeting.",
        "answer2": "Bind mounts map a host path; volumes are managed by Docker.",
        "answer3": "",
    }
    assert result.untagged_cells == {}
