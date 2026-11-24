"""Celery task: extract answers from a submission's raw file and grade each question.

Beanie/Motor are async; Celery tasks run sync, so each task wraps its DB work
in asyncio.run(...) and initializes Beanie fresh (a new event loop needs its
own Motor client) -- fine for the one-shot, short-lived nature of this task.
"""

import asyncio
import base64
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from beanie import PydanticObjectId
from langchain_core.language_models.chat_models import BaseChatModel

from .. import storage
from ..db import init_db
from ..documents import (
    AnsweredQuestion,
    GradingTrace,
    SegmentationSource,
    Submission,
    SubmissionStatus,
    resolve_llm_model,
)
from ..grading.extraction import FORMAT_FILE_INFO, SEGMENTED_FORMATS, extract_answers
from ..grading.extraction.pdf_generic import (
    MARKDOWN_NAME,
    convert_pdf,
    extract_segmented,
    missing_questions,
    resolve_segments,
)
from ..grading.grading import grade_answer
from ..grading.llm import get_chat_model
from ..grading.models import ConvertedDocument, DocumentImage, ExtractedImage, ExtractionResult, GradeTrace, Question
from ..grading.segmentation import suggest_segments
from .celery_app import celery_app

logger = logging.getLogger(__name__)

# See api/routers/submissions.py's _SHALLOW_LINKS -- grading only needs
# rubric.questions, never rubric.course/rubric.edition.
_SHALLOW_RUBRIC_LINK = {"rubric": 1}


@celery_app.task(name="aitana.grade_submission")
def grade_submission(submission_id: str, only_question_id: str | None = None, model_id: str | None = None) -> None:
    asyncio.run(_grade_submission(submission_id, only_question_id, model_id))


async def _grade_submission(
    submission_id: str, only_question_id: str | None = None, model_id: str | None = None
) -> None:
    """`only_question_id` re-grades just that question: every other answer
    keeps its previous LLM grade (see `_rebuild_answers`) and is not sent to
    the LLM again -- for re-running one question after a rubric tweak.

    `model_id` is the `LlmModel` to grade with (the API always resolves and
    passes one); `None` falls back to the current default, for tasks
    enqueued before model selection existed."""
    submission = await _load_submission(submission_id)
    if submission is None:
        return

    rubric = submission.rubric
    if rubric.format in SEGMENTED_FORMATS:
        if submission.document is None:
            # Conversion never succeeded (or never ran): a re-grade retries
            # it, and ends up waiting for segmentation like a fresh upload.
            await _prepare(submission, model_id)
            return
        if missing := missing_questions(submission.segments, [q.id for q in rubric.questions]):
            # E.g. the rubric gained a question since segmenting -- grading
            # would silently treat it as blank, so ask a professor instead.
            logger.info("Submission %s needs segmentation for %s before grading", submission_id, missing)
            submission.status = SubmissionStatus.AWAITING_SEGMENTATION
            submission.error = None
            await submission.save()
            return

    try:
        submission.status = SubmissionStatus.EXTRACTING
        await submission.save()

        extracted = extract_submission_file(submission)

        submission.status = SubmissionStatus.GRADING
        submission.answers = _rebuild_answers(
            rubric.questions, extracted, submission.answers, keep_grades_except=only_question_id
        )
        await submission.save()

        llm_model = await resolve_llm_model(model_id)
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
        await _grade_answers(
            submission,
            rubric.questions,
            chat_model,
            rubric.grading_scale,
            llm_model.provider,
            llm_model.model,
            reasoning_effort=llm_model.reasoning_effort,
            only_question_id=only_question_id,
            images=extracted.images,
            rubric_context=rubric.context,
        )

        submission.status = SubmissionStatus.GRADED
        await submission.save()
    except Exception as exc:  # noqa: BLE001 -- one bad submission shouldn't crash the worker
        logger.exception("Grading submission %s failed", submission_id)
        submission.status = SubmissionStatus.FAILED
        submission.error = str(exc)
        await submission.save()


@celery_app.task(name="aitana.prepare_submission")
def prepare_submission(submission_id: str, segment_model_id: str | None = None) -> None:
    """Free-form PDFs: convert to Markdown (+ images), then, with
    `segment_model_id`, ask that model for a segmentation suggestion. Always
    ends in `awaiting_segmentation` (or `failed` if conversion failed) --
    grading only starts once a professor confirms the segments."""
    asyncio.run(_prepare_submission(submission_id, segment_model_id))


@celery_app.task(name="aitana.suggest_segments")
def suggest_segments_task(submission_id: str, model_id: str | None = None) -> None:
    asyncio.run(_suggest_segments_by_id(submission_id, model_id))


async def _load_submission(submission_id: str) -> Submission | None:
    await init_db()
    submission = await Submission.get(
        PydanticObjectId(submission_id), fetch_links=True, nesting_depths_per_field=_SHALLOW_RUBRIC_LINK
    )
    if submission is None:
        logger.error("Submission %s not found", submission_id)
    return submission


async def _prepare_submission(submission_id: str, segment_model_id: str | None) -> None:
    if (submission := await _load_submission(submission_id)) is not None:
        await _prepare(submission, segment_model_id)


async def _suggest_segments_by_id(submission_id: str, model_id: str | None) -> None:
    if (submission := await _load_submission(submission_id)) is not None:
        await _suggest(submission, model_id)


async def _prepare(submission: Submission, segment_model_id: str | None) -> None:
    try:
        submission.status = SubmissionStatus.CONVERTING
        submission.error = None
        await submission.save()

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "submission.pdf"
            storage.download_to_path(submission.file_object_key, pdf_path)
            converted = convert_pdf(pdf_path)

        markdown_key = storage.derived_object_key(submission.file_object_key, MARKDOWN_NAME)
        storage.upload_file(markdown_key, converted.markdown.encode("utf-8"), "text/markdown; charset=utf-8")
        images = []
        for img in converted.images:
            key = storage.derived_object_key(submission.file_object_key, img.name)
            storage.upload_file(key, img.data, img.mime_type)
            images.append(
                DocumentImage(name=img.name, object_key=key, line=img.line, page=img.page, mime_type=img.mime_type)
            )
        logger.info(
            "Submission %s converted: %d line(s), %d page(s), %d image(s)",
            submission.id,
            converted.line_count,
            len(converted.page_start_lines),
            len(images),
        )

        # Old segments refer to the old Markdown's line numbers -- drop them.
        submission.document = ConvertedDocument(
            markdown_object_key=markdown_key,
            line_count=converted.line_count,
            page_start_lines=converted.page_start_lines,
            images=images,
        )
        submission.segments = None
        submission.segmentation_source = None
        submission.segmented_at = None
        submission.status = SubmissionStatus.AWAITING_SEGMENTATION
        await submission.save()
    except Exception as exc:  # noqa: BLE001 -- same policy as grading
        logger.exception("Converting submission %s failed", submission.id)
        submission.status = SubmissionStatus.FAILED
        submission.error = f"PDF conversion failed: {exc}"
        await submission.save()
        return

    if segment_model_id is not None:
        await _suggest(submission, segment_model_id)


async def _suggest(submission: Submission, model_id: str | None) -> None:
    """LLM segmentation suggestion. A failure is not fatal: the submission
    still ends up `awaiting_segmentation` (with `error` set) so a professor
    can segment it by hand."""
    if submission.document is None:
        logger.error("Submission %s has no converted document to segment", submission.id)
        return
    submission.status = SubmissionStatus.SEGMENTING
    submission.error = None
    await submission.save()
    try:
        markdown = storage.download_bytes(submission.document.markdown_object_key).decode("utf-8")
        llm_model = await resolve_llm_model(model_id)
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
        segments = await asyncio.to_thread(suggest_segments, chat_model, markdown, submission.rubric.questions)
        submission.segments = segments
        submission.segmentation_source = SegmentationSource.LLM
        submission.segmented_at = datetime.now(UTC)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM segmentation of submission %s failed", submission.id)
        submission.error = f"LLM segmentation failed: {exc}"
    submission.status = SubmissionStatus.AWAITING_SEGMENTATION
    await submission.save()


def extract_submission_file(submission: Submission) -> ExtractionResult:
    """Download the submission's stored file and run its rubric's extractor.
    `submission.rubric` must be resolved. Also used outside grading to get
    images back (they're never stored, see AGENTS.md "Cell outputs and image
    inputs").

    Free-form PDFs (`SEGMENTED_FORMATS`) are sliced from their stored
    Markdown + segments instead; only the images of `include_images`
    questions are downloaded."""
    if submission.rubric.format in SEGMENTED_FORMATS:
        return _extract_segmented_submission(submission)
    extension, _content_type = FORMAT_FILE_INFO[submission.rubric.format]
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / f"submission{extension}"
        storage.download_to_path(submission.file_object_key, file_path)
        return extract_answers(submission.rubric.format, file_path)


def _extract_segmented_submission(submission: Submission) -> ExtractionResult:
    document = submission.document
    if document is None:
        raise RuntimeError("This PDF has not been converted yet")
    markdown = storage.download_bytes(document.markdown_object_key).decode("utf-8")
    questions = submission.rubric.questions
    segments = submission.segments or []
    spans = resolve_segments(segments, document.line_count)
    wanted = [spans[q.id] for q in questions if q.include_images and q.id in spans]
    images = [
        (
            img.line,
            ExtractedImage(mime_type=img.mime_type, data=base64.b64encode(storage.download_bytes(img.object_key)).decode()),
        )
        for img in document.images
        if any(start <= img.line <= end for start, end, _ in wanted)
    ]
    return extract_segmented(markdown, segments, questions, images)


def _rebuild_answers(
    questions: list[Question],
    extracted: ExtractionResult,
    previous: list[AnsweredQuestion],
    keep_grades_except: str | None = None,
) -> list[AnsweredQuestion]:
    """Fresh, ungraded answers for every question in the rubric, carrying
    over any TA manual override (`manual_grade`/`manual_graded_at`) from the
    previous run by `question_id` -- a re-grade refreshes the LLM's `grade`,
    but must not silently throw away a human correction. An override for a
    question no longer in the rubric is dropped along with the question.

    With `keep_grades_except` set (single-question re-grade), the previous
    LLM `grade` is also carried over for every *other* question.

    `student_answer` includes the cell outputs when `q.include_outputs` is
    set; `image_count` is what `_grade_answers` will attach for
    `q.include_images`."""
    previous_by_id = {a.question_id: a for a in previous}
    answers = []
    for q in questions:
        answer_text = extracted.answers.get(q.answer_key, "")
        if q.include_outputs:
            answer_text = extracted.answers_with_outputs.get(q.answer_key, answer_text)
        answer = AnsweredQuestion(
            question_id=q.id,
            student_answer=answer_text,
            untagged_cells=extracted.untagged_cells.get(q.answer_key, 0),
            image_count=len(extracted.images.get(q.answer_key, [])) if q.include_images else 0,
        )
        if (prev := previous_by_id.get(q.id)) is not None:
            answer.manual_grade = prev.manual_grade
            answer.manual_graded_at = prev.manual_graded_at
            if keep_grades_except is not None and q.id != keep_grades_except:
                answer.grade = prev.grade
                answer.grading_info = prev.grading_info
        answers.append(answer)
    return answers


async def _grade_answers(
    submission: Submission,
    questions: list[Question],
    chat_model: BaseChatModel,
    grading_scale: dict[str, str],
    provider: str,
    model: str,
    *,
    reasoning_effort: str | None = None,
    only_question_id: str | None = None,
    images: dict[str, list[ExtractedImage]] | None = None,
    rubric_context: str | None = None,
) -> None:
    """Grade each question in order, saving after every single one.

    Re-indexes into `submission.answers[i]` fresh each iteration rather than
    holding object references from a single zip() bound before the loop
    started: `Document.save()` re-parses the whole document and replaces
    `answers` with newly-constructed `AnsweredQuestion` instances every time
    (Beanie's `validate_on_save`), so a reference captured before any
    `save()` call goes stale after the first one -- mutating it is a no-op
    that silently drops every grade past the first question.

    Also persists a `GradingTrace` document per question, captured via
    `grade_answer`'s `on_trace` hook -- see `documents/grading_trace.py` for
    why this is a separate collection rather than embedded in `submission`.
    Inserted before `submission.save()` so a trace exists even if something
    about the submission save itself goes wrong -- the grade was still
    produced, and that's the part worth not losing.

    The trace's compact `info` summary is also stored on the answer itself
    (`AnsweredQuestion.grading_info`) for the submission page's tooltip.

    `only_question_id` skips every other question (no LLM call, no trace).
    `images` (`ExtractionResult.images`, by answer key) are attached only for
    questions with `include_images`.
    """
    for i, question in enumerate(questions):
        if only_question_id is not None and question.id != only_question_id:
            continue
        captured_trace: GradeTrace | None = None

        def _capture(trace: GradeTrace) -> None:
            nonlocal captured_trace
            captured_trace = trace

        grade = grade_answer(
            chat_model,
            question,
            submission.answers[i].student_answer,
            grading_scale,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            on_trace=_capture,
            images=(images or {}).get(question.answer_key, []) if question.include_images else (),
            rubric_context=rubric_context,
        )
        if captured_trace is not None:
            await GradingTrace(submission=submission, question_id=question.id, trace=captured_trace).insert()

        submission.answers[i].grade = grade
        submission.answers[i].grading_info = captured_trace.info if captured_trace is not None else None
        await submission.save()
