"""Generic-document conversion + segment slicing (grading/extraction/generic_document.py)."""

import httpx
import pymupdf
import pytest

from aitana.grading.extraction.generic_document import (
    DocumentConversionError,
    SegmentationError,
    convert_pdf,
    document_to_pdf,
    extract_segmented,
    missing_questions,
    number_lines,
    resolve_segments,
    validate_segments,
)
from aitana.grading.models import AnswerSegment, ExtractedImage, Question


def _question(qid: str, **overrides) -> Question:
    return Question(id=qid, title=qid, question="?", rubric="R", **overrides)


@pytest.fixture
def two_page_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Exercise 1", fontsize=16)
    page.insert_text((72, 100), "The answer is docker run hello-world.")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(72, 120, 312, 280), pixmap=pix)
    page = doc.new_page()
    page.insert_text((72, 72), "Exercise 2", fontsize=16)
    page.insert_text((72, 100), "Containers share the host kernel.")
    path = tmp_path / "submission.pdf"
    doc.save(path)
    return path


def test_convert_pdf_tracks_pages_and_images(two_page_pdf):
    out = convert_pdf(two_page_pdf)
    lines = out.markdown.split("\n")

    assert len(out.page_start_lines) == 2
    assert "Exercise 1" in lines[out.page_start_lines[0] - 1]
    assert "Exercise 2" in lines[out.page_start_lines[1] - 1]
    assert "Containers share the host kernel." in out.markdown

    [image] = out.images
    assert image.name == "img-0001.png"
    assert image.page == 1
    assert image.mime_type == "image/png"
    assert image.data.startswith(b"\x89PNG")
    assert lines[image.line - 1] == "![](img-0001.png)"
    assert image.line < out.page_start_lines[1]


def test_resolve_segments_fills_implicit_ends():
    segments = [
        AnswerSegment(question_id="q2", start_line=10),
        AnswerSegment(question_id="q1", start_line=2, end_line=5),
        AnswerSegment(question_id="q3", start_line=None),
        AnswerSegment(question_id="q4", start_line=15),
    ]
    resolved = resolve_segments(segments, line_count=20)
    assert resolved == {"q1": (2, 5, "q1"), "q2": (10, 14, "q2"), "q4": (15, 20, "q4")}
    assert list(resolved) == ["q1", "q2", "q4"]  # sorted by start


@pytest.mark.parametrize(
    "segments, message",
    [
        ([AnswerSegment(question_id="zz", start_line=1)], "Unknown question"),
        ([AnswerSegment(question_id="q1", start_line=0)], "out of range"),
        ([AnswerSegment(question_id="q1", start_line=11)], "out of range"),
        ([AnswerSegment(question_id="q1", start_line=5, end_line=4)], "end line"),
        ([AnswerSegment(question_id="q1", start_line=None, end_line=4)], "needs a start line"),
        (
            [AnswerSegment(question_id="q1", start_line=3), AnswerSegment(question_id="q2", start_line=3)],
            "same line",
        ),
        (
            [AnswerSegment(question_id="q1", start_line=1, end_line=6), AnswerSegment(question_id="q2", start_line=5)],
            "overlaps",
        ),
        ([AnswerSegment(question_id="q1", start_line=1), AnswerSegment(question_id="q1", start_line=5)], "more than one"),
    ],
)
def test_validate_segments_rejects(segments, message):
    with pytest.raises(SegmentationError, match=message):
        validate_segments(segments, line_count=10, question_ids=["q1", "q2"])


def test_validate_segments_accepts_out_of_order_and_gaps():
    validate_segments(
        [AnswerSegment(question_id="q2", start_line=1, end_line=3), AnswerSegment(question_id="q1", start_line=6)],
        line_count=10,
        question_ids=["q1", "q2"],
    )


def test_missing_questions_counts_no_answer_as_decided():
    segments = [AnswerSegment(question_id="q1", start_line=None)]
    assert missing_questions(segments, ["q1", "q2"]) == ["q2"]
    assert missing_questions(None, ["q1"]) == ["q1"]


def test_extract_segmented_slices_lines_and_assigns_images():
    markdown = "\n".join(["# Exercise 1", "answer one", "![](img-0001.png)", "dead line", "# Exercise 2", "answer two"])
    questions = [_question("q1", field="ex1"), _question("q2"), _question("q3")]
    segments = [
        AnswerSegment(question_id="q1", start_line=1, end_line=3),
        AnswerSegment(question_id="q2", start_line=5),
        AnswerSegment(question_id="q3", start_line=None),
    ]
    image = ExtractedImage(mime_type="image/png", data="AAAA")

    result = extract_segmented(markdown, segments, questions, images=[(3, image)])

    assert result.answers == {
        "ex1": "# Exercise 1\nanswer one\n![](img-0001.png)",
        "q2": "# Exercise 2\nanswer two",
        "q3": "",
    }
    assert result.images == {"ex1": [image]}


def test_number_lines_hides_images_and_truncates():
    numbered = number_lines("a\n![](img-0001.png)\n" + "x" * 50, max_line_length=10)
    assert numbered.split("\n") == ["1| a", "2| [image]", "3| " + "x" * 10 + " [...]"]


def test_document_to_pdf_converts_docx(minimal_docx):
    pdf = document_to_pdf(minimal_docx("Hello answer"), ".docx")
    assert pdf.startswith(b"%PDF")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert "Hello answer" in doc[0].get_text()


def test_document_to_pdf_rejects_unreadable_file():
    with pytest.raises(DocumentConversionError, match="could not convert the .docx file"):
        document_to_pdf(b"not a docx", ".docx")


def _blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "converted by LibreOffice")
    return doc.tobytes()


def test_document_to_pdf_uses_gotenberg_when_configured(monkeypatch):
    calls = []

    def fake_post(url, *, files, timeout):
        calls.append((url, files, timeout))
        return httpx.Response(200, content=_blank_pdf())

    monkeypatch.setattr(httpx, "post", fake_post)
    pdf = document_to_pdf(b"docx bytes", ".docx", gotenberg_url="http://gotenberg:3000/", timeout=5)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert "converted by LibreOffice" in doc[0].get_text()
    [(url, files, timeout)] = calls
    assert url == "http://gotenberg:3000/forms/libreoffice/convert"
    assert files == {"files": ("document.docx", b"docx bytes")}
    assert timeout == 5


def test_document_to_pdf_gotenberg_error_is_not_a_silent_fallback(monkeypatch, minimal_docx):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(500, text="LibreOffice crashed"))
    with pytest.raises(DocumentConversionError, match="converter answered 500: LibreOffice crashed"):
        document_to_pdf(minimal_docx("x"), ".docx", gotenberg_url="http://gotenberg:3000")


def test_document_to_pdf_gotenberg_unreachable(monkeypatch, minimal_docx):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fail)
    with pytest.raises(DocumentConversionError, match="could not reach the document converter"):
        document_to_pdf(minimal_docx("x"), ".docx", gotenberg_url="http://gotenberg:3000")


def test_document_to_pdf_gotenberg_garbage_response(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, content=b"not a pdf"))
    with pytest.raises(DocumentConversionError, match="unreadable PDF"):
        document_to_pdf(b"docx", ".docx", gotenberg_url="http://gotenberg:3000")
