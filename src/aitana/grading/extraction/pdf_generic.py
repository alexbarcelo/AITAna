"""Free-form PDF submissions: Markdown conversion and segmentation.

Unlike `pdf.py` (AcroForm fields name each answer) a free-form PDF has no
structure telling which part answers which question. The flow is:

1. `convert_pdf` turns the PDF into one Markdown document (pymupdf4llm)
   plus the images found in it, once, right after upload. Image references
   are rewritten to stable names (`![](img-0003.png)`), so each image sits
   on one Markdown line.
2. Someone decides where each answer starts (and optionally ends) --
   "segmentation". The professor does it in the frontend's segmentation
   editor, optionally starting from an LLM suggestion
   (`grading/segmentation.py`). Segments are 1-based Markdown line numbers
   (`AnswerSegment`), so the stored Markdown is the single coordinate
   system shared by the LLM, the editor and grading -- it's never silently
   regenerated, since a different conversion could shift every line.
3. `extract_segmented` slices the Markdown by those segments into an
   `ExtractionResult`, the same shape every other format's extractor
   returns; an image belongs to the answer whose segment contains its line.

Everything here is pure (no DB, no MinIO) -- the worker does the storage
around it (`worker/tasks.py`).
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..models import AnswerSegment, ExtractedImage, ExtractionResult, Question

logger = logging.getLogger(__name__)

MARKDOWN_NAME = "document.md"

# `![alt](target)` -- pymupdf4llm writes images as `![](<image_path>/<name>)`.
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class ConvertedImage:
    name: str
    data: bytes
    mime_type: str
    line: int
    page: int


@dataclass
class ConversionOutput:
    markdown: str
    page_start_lines: list[int] = field(default_factory=list)
    images: list[ConvertedImage] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return len(self.markdown.split("\n"))


def _normalize_page(text: str) -> list[str]:
    """Strip trailing whitespace, collapse runs of blank lines into one and
    drop leading/trailing blank lines -- fewer meaningless lines to number."""
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line and (not lines or not lines[-1]):
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _to_markdown_chunks(path: Path, image_dir: Path) -> list:
    """`Document.to_markdown` (PyMuPDF >= 2.0; it delegates to pymupdf4llm,
    which must be installed, but pymupdf4llm's own docs advise calling it
    through `Document` rather than its module functions).

    OCR (for scanned pages) needs a working Tesseract; a partially installed
    one (binary present, language data missing) makes the whole conversion
    raise, so on failure retry once without OCR -- text-based PDFs, the
    common case, lose nothing."""
    import pymupdf  # heavy import, only needed where conversion actually runs

    kwargs = dict(page_chunks=True, write_images=True, image_path=str(image_dir), show_progress=False)
    with pymupdf.open(path) as doc:
        try:
            return doc.to_markdown(**kwargs)
        except Exception:  # noqa: BLE001 -- pymupdf raises library-specific error types
            logger.warning("PDF conversion with OCR failed, retrying without OCR", exc_info=True)
    for leftover in image_dir.iterdir():
        leftover.unlink()
    with pymupdf.open(path) as doc:
        return doc.to_markdown(use_ocr=False, **kwargs)


def convert_pdf(path: Path) -> ConversionOutput:
    """Convert a PDF into Markdown + images (see module docstring)."""
    with tempfile.TemporaryDirectory() as tmp:
        image_dir = Path(tmp)
        chunks = _to_markdown_chunks(path, image_dir)

        lines: list[str] = []
        page_start_lines: list[int] = []
        images: list[ConvertedImage] = []
        names_by_source: dict[str, str] = {}

        for page_number, chunk in enumerate(chunks, start=1):
            text = chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)

            def _rename(match: re.Match[str], page: int = page_number) -> str:
                source = Path(match.group(1)).name
                image_file = image_dir / source
                if not image_file.is_file():
                    return match.group(0)
                name = names_by_source.get(source)
                if name is None:
                    ext = image_file.suffix.lower()
                    name = f"img-{len(images) + 1:04d}{ext}"
                    names_by_source[source] = name
                    images.append(
                        ConvertedImage(
                            name=name,
                            data=image_file.read_bytes(),
                            mime_type=_MIME_BY_EXT.get(ext, "application/octet-stream"),
                            line=0,  # filled below, once line numbers are final
                            page=page,
                        )
                    )
                return f"![]({name})"

            page_lines = _normalize_page(_IMAGE_REF_RE.sub(_rename, text))
            if lines and page_lines:
                lines.append("")  # one blank line between pages
            page_start_lines.append(len(lines) + 1)
            lines.extend(page_lines)

    if not lines:
        lines = [""]
    by_name = {img.name: img for img in images}
    for number, line in enumerate(lines, start=1):
        for match in _IMAGE_REF_RE.finditer(line):
            if (img := by_name.get(match.group(1))) is not None and img.line == 0:
                img.line = number
    # Never leave an image without a line (shouldn't happen): pin it to its page start.
    for img in images:
        if img.line == 0:
            img.line = min(page_start_lines[img.page - 1], len(lines))

    return ConversionOutput(markdown="\n".join(lines), page_start_lines=page_start_lines, images=images)


class SegmentationError(ValueError):
    """Invalid segments for a document (bad line numbers, overlaps, unknown
    question ids). The message is meant for the professor."""


def validate_segments(
    segments: Sequence[AnswerSegment], line_count: int, question_ids: Iterable[str]
) -> None:
    known = set(question_ids)
    seen: set[str] = set()
    for seg in segments:
        if seg.question_id not in known:
            raise SegmentationError(f"Unknown question {seg.question_id!r}")
        if seg.question_id in seen:
            raise SegmentationError(f"Question {seg.question_id!r} has more than one segment")
        seen.add(seg.question_id)
        if seg.start_line is None:
            if seg.end_line is not None:
                raise SegmentationError(f"Question {seg.question_id!r}: an end line needs a start line")
            continue
        if not 1 <= seg.start_line <= line_count:
            raise SegmentationError(f"Question {seg.question_id!r}: start line {seg.start_line} is out of range")
        if seg.end_line is not None and not seg.start_line <= seg.end_line <= line_count:
            raise SegmentationError(
                f"Question {seg.question_id!r}: end line {seg.end_line} must be between its start line and {line_count}"
            )

    starts = sorted((s.start_line, s.question_id) for s in segments if s.start_line is not None)
    for (start_a, qa), (start_b, qb) in zip(starts, starts[1:], strict=False):
        if start_a == start_b:
            raise SegmentationError(f"Questions {qa!r} and {qb!r} start on the same line ({start_a})")
    spans = list(resolve_segments(segments, line_count).values())
    for (start, end, qid), (next_start, _next_end, next_qid) in zip(spans, spans[1:], strict=False):
        if end >= next_start:
            raise SegmentationError(f"Question {qid!r} (lines {start}-{end}) overlaps question {next_qid!r}")


def resolve_segments(segments: Sequence[AnswerSegment], line_count: int) -> dict[str, tuple[int, int, str]]:
    """`{question_id: (start, end, question_id)}` for every segment with a
    start line, sorted by start, with implicit ends filled in: the line
    before the next segment's start, or the last line of the document."""
    placed = sorted((s for s in segments if s.start_line is not None), key=lambda s: s.start_line or 0)
    resolved: dict[str, tuple[int, int, str]] = {}
    for i, seg in enumerate(placed):
        assert seg.start_line is not None
        if seg.end_line is not None:
            end = seg.end_line
        elif i + 1 < len(placed):
            end = (placed[i + 1].start_line or 0) - 1
        else:
            end = line_count
        resolved[seg.question_id] = (seg.start_line, end, seg.question_id)
    return resolved


def missing_questions(segments: Sequence[AnswerSegment] | None, question_ids: Iterable[str]) -> list[str]:
    """Question ids with no segment decided yet (an explicit "no answer"
    segment counts as decided)."""
    decided = {s.question_id for s in segments or []}
    return [qid for qid in question_ids if qid not in decided]


def extract_segmented(
    markdown: str,
    segments: Sequence[AnswerSegment],
    questions: Sequence[Question],
    images: Sequence[tuple[int, ExtractedImage]] = (),
) -> ExtractionResult:
    """Slice the converted Markdown into one answer per question.

    `images` is `(line, image)` pairs for the document's images (only the
    ones the caller bothered to load -- usually those of questions with
    `include_images`). An image goes to the answer whose segment contains
    its line. Questions with no segment / `start_line=None` get an empty
    answer, which grading treats as blank."""
    lines = markdown.split("\n")
    resolved = resolve_segments(segments, len(lines))
    result = ExtractionResult()
    for q in questions:
        span = resolved.get(q.id)
        if span is None:
            result.answers[q.answer_key] = ""
            continue
        start, end, _ = span
        result.answers[q.answer_key] = "\n".join(lines[start - 1 : end]).strip()
        found = [img for line, img in images if start <= line <= end]
        if found:
            result.images[q.answer_key] = found
    return result


def number_lines(markdown: str, max_line_length: int = 300) -> str:
    """The Markdown as the segmentation LLM sees it: every line prefixed
    with its 1-based number, image references replaced by `[image]`, very
    long lines truncated (they matter for grading, not for finding where an
    answer starts)."""
    lines = markdown.split("\n")
    width = len(str(len(lines)))
    out = []
    for number, line in enumerate(lines, start=1):
        line = _IMAGE_REF_RE.sub("[image]", line)
        if len(line) > max_line_length:
            line = line[:max_line_length] + " [...]"
        out.append(f"{number:>{width}}| {line}")
    return "\n".join(out)
