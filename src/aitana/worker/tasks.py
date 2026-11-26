"""Celery tasks: convert/segment generic documents, extract answers from a
submission's stored file and grade each question.

Celery tasks run sync and the database layer is async, so each task runs
its work on its worker process's own long-lived event loop (`_run_async`)
with its own unpooled engine (`db.session.task_session`). Not a fresh
`asyncio.run(...)` per task: the OpenAI/Anthropic integrations cache one
async HTTP client per process, whose pooled connections belong to the loop
that first used them -- closing that loop after each task would break the
next task's LLM calls.

A submission's questions are graded concurrently (`GRADING_CONCURRENCY` at
a time) and independently: one failing question doesn't stop the others.

Every state change is committed right away, so polling clients see progress
(including each graded question). A submission deleted while a task works on
it (`DELETE ...?force=true`) makes the next commit match no row
(`StaleDataError`): the task logs it and stops.
"""

import asyncio
import base64
import logging
import os
import tempfile
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from celery.signals import worker_process_shutdown
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from .. import storage
from ..db import (
    AnsweredQuestion,
    GradingTrace,
    SegmentationSource,
    Submission,
    SubmissionStatus,
    resolve_llm_model,
)
from ..db.session import task_session
from ..grading.extraction import FORMAT_FILE_INFO, SEGMENTED_FORMATS, extract_answers
from ..grading.extraction.generic_document import (
    MARKDOWN_NAME,
    convert_pdf,
    extract_segmented,
    missing_questions,
    resolve_segments,
)
from ..grading.general_feedback import GeneralFeedbackItem, has_general_feedback, write_general_feedback
from ..grading.grading import grade_answer
from ..grading.llm import get_chat_model
from ..grading.models import (
    ConvertedDocument,
    DocumentImage,
    ExtractedImage,
    ExtractionResult,
    Grade,
    GradeTrace,
    GradingLevel,
    Question,
)
from ..grading.segmentation import suggest_segments
from ..settings import get_settings
from .celery_app import celery_app

logger = logging.getLogger(__name__)


class SubmissionGone(Exception):
    """The submission row disappeared mid-task (deleted with `force`)."""


_runner: asyncio.Runner | None = None
_runner_pid: int | None = None


def _run_async(coro: Coroutine[Any, Any, None]) -> None:
    """Run a task body on this worker process's event loop, created on first
    use and kept for the process's lifetime (see the module docstring for
    why not `asyncio.run`). Per pid, so a loop never crosses a fork. Assumes
    one task at a time per process -- Celery's default prefork pool."""
    global _runner, _runner_pid
    if _runner is None or _runner_pid != os.getpid():
        _runner = asyncio.Runner()
        _runner_pid = os.getpid()
    _runner.run(coro)


@worker_process_shutdown.connect
def _close_runner(**_kwargs: Any) -> None:
    global _runner
    if _runner is not None and _runner_pid == os.getpid():
        _runner.close()
        _runner = None


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except StaleDataError as exc:
        await session.rollback()
        raise SubmissionGone from exc


async def _mark(session: AsyncSession, submission_id: int, status: SubmissionStatus, error: str | None) -> None:
    """Set status/error after a failure, with a fresh UPDATE: the session is
    rolled back first (it may hold a failed flush), which expires every
    loaded object."""
    await session.rollback()
    await session.execute(
        update(Submission)
        .where(Submission.id == submission_id)
        .values(status=status, error=error, updated_at=datetime.now(UTC))
    )
    await session.commit()


async def _load_submission(session: AsyncSession, submission_id: int) -> Submission | None:
    submission = await session.get(Submission, submission_id, options=(selectinload(Submission.rubric),))
    if submission is None:
        logger.error("Submission %s not found", submission_id)
    return submission


@celery_app.task(name="aitana.grade_submission")
def grade_submission(submission_id: int, only_question_id: str | None = None, model_id: int | None = None) -> None:
    _run_async(_run_grade_submission(submission_id, only_question_id, model_id))


async def _run_grade_submission(submission_id: int, only_question_id: str | None, model_id: int | None) -> None:
    async with task_session() as session:
        try:
            await _grade_submission(session, submission_id, only_question_id, model_id)
        except SubmissionGone:
            logger.warning("Submission %s was deleted while being processed", submission_id)


async def _grade_submission(
    session: AsyncSession, submission_id: int, only_question_id: str | None = None, model_id: int | None = None
) -> None:
    """`only_question_id` re-grades just that question: every other answer
    keeps its previous LLM grade (see `_rebuild_answers`) and is not sent to
    the LLM again -- for re-running one question after a rubric tweak.

    `model_id` is the `LlmModel` to grade with (the API always resolves and
    passes one); `None` falls back to the current default."""
    submission = await _load_submission(session, submission_id)
    if submission is None:
        return

    rubric = submission.rubric
    if rubric.format in SEGMENTED_FORMATS:
        if submission.document is None:
            # Conversion never succeeded (or never ran): a re-grade retries
            # it, and ends up waiting for segmentation like a fresh upload.
            await _prepare(session, submission, model_id)
            return
        if missing := missing_questions(submission.segments, [q.id for q in rubric.questions]):
            # E.g. the rubric gained a question since segmenting -- grading
            # would silently treat it as blank, so ask a professor instead.
            logger.info("Submission %s needs segmentation for %s before grading", submission_id, missing)
            submission.status = SubmissionStatus.AWAITING_SEGMENTATION
            submission.error = None
            await _commit(session)
            return

    try:
        submission.status = SubmissionStatus.EXTRACTING
        await _commit(session)

        extracted = extract_submission_file(submission)

        # Persisted before the LLM client is even built: a bad provider
        # config still leaves the extracted answers visible.
        submission.status = SubmissionStatus.GRADING
        submission.answers = _rebuild_answers(
            rubric.questions, extracted, submission.answers, keep_grades_except=only_question_id
        )
        # Based on the old grades: rewritten once grading is done.
        submission.general_feedback = None
        await _commit(session)

        llm_model = await resolve_llm_model(session, model_id)
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
        await _grade_answers(
            session,
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
        if failed := [a.question_id for a in submission.answers if a.error is not None]:
            # Every other question is graded and kept; a regrade of just
            # these (or of the whole submission) retries them. No general
            # feedback: it would digest an incomplete grading.
            submission.status = SubmissionStatus.FAILED
            submission.error = (
                f"Grading failed for {len(failed)} of {len(submission.answers)} question(s): {', '.join(failed)}. "
                "See each answer's error; regrade to retry."
            )
        else:
            if has_general_feedback(rubric.general_feedback_instructions):
                await _write_general_feedback(
                    submission, chat_model, llm_model.provider, llm_model.model, llm_model.reasoning_effort
                )
            submission.status = SubmissionStatus.GRADED
        submission.updated_at = datetime.now(UTC)
        await _commit(session)
    except SubmissionGone:
        raise
    except Exception as exc:  # noqa: BLE001 -- one bad submission shouldn't crash the worker
        logger.exception("Grading submission %s failed", submission_id)
        await _mark(session, submission_id, SubmissionStatus.FAILED, str(exc))


@celery_app.task(name="aitana.regenerate_general_feedback")
def regenerate_general_feedback(submission_id: int, model_id: int | None = None) -> None:
    """Rewrite only a graded submission's general feedback, from its current
    effective grades (e.g. after a TA overrode some) -- no re-extraction, no
    per-question LLM calls."""
    _run_async(_run_regenerate_general_feedback(submission_id, model_id))


async def _run_regenerate_general_feedback(submission_id: int, model_id: int | None) -> None:
    async with task_session() as session:
        try:
            await _regenerate_general_feedback(session, submission_id, model_id)
        except SubmissionGone:
            logger.warning("Submission %s was deleted while being processed", submission_id)


async def _regenerate_general_feedback(session: AsyncSession, submission_id: int, model_id: int | None = None) -> None:
    """The API set the submission to `grading` before enqueueing (which
    keeps manual edits out meanwhile); this always puts it back to `graded`
    -- a general feedback failure never touches the per-question grades."""
    submission = await _load_submission(session, submission_id)
    if submission is None:
        return
    try:
        llm_model = await resolve_llm_model(session, model_id)
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
    except Exception as exc:  # noqa: BLE001
        logger.exception("General feedback of submission %s failed", submission_id)
        submission.error = f"General feedback failed: {exc}"
    else:
        await _write_general_feedback(
            submission, chat_model, llm_model.provider, llm_model.model, llm_model.reasoning_effort
        )
    submission.status = SubmissionStatus.GRADED
    submission.updated_at = datetime.now(UTC)
    await _commit(session)


@celery_app.task(name="aitana.prepare_submission")
def prepare_submission(submission_id: int, segment_model_id: int | None = None) -> None:
    """Generic documents: convert to Markdown (+ images), then, with
    `segment_model_id`, ask that model for a segmentation suggestion. Always
    ends in `awaiting_segmentation` (or `failed` if conversion failed) --
    grading only starts once a professor confirms the segments."""
    _run_async(_run_prepare_submission(submission_id, segment_model_id))


@celery_app.task(name="aitana.suggest_segments")
def suggest_segments_task(submission_id: int, model_id: int | None = None) -> None:
    _run_async(_run_suggest_segments(submission_id, model_id))


async def _run_prepare_submission(submission_id: int, segment_model_id: int | None) -> None:
    async with task_session() as session:
        try:
            if (submission := await _load_submission(session, submission_id)) is not None:
                await _prepare(session, submission, segment_model_id)
        except SubmissionGone:
            logger.warning("Submission %s was deleted while being processed", submission_id)


async def _run_suggest_segments(submission_id: int, model_id: int | None) -> None:
    async with task_session() as session:
        try:
            if (submission := await _load_submission(session, submission_id)) is not None:
                await _suggest(session, submission, model_id)
        except SubmissionGone:
            logger.warning("Submission %s was deleted while being processed", submission_id)


async def _prepare(session: AsyncSession, submission: Submission, segment_model_id: int | None) -> None:
    submission_id = submission.id
    try:
        submission.status = SubmissionStatus.CONVERTING
        submission.error = None
        await _commit(session)

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
            submission_id,
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
        await _commit(session)
    except SubmissionGone:
        raise
    except Exception as exc:  # noqa: BLE001 -- same policy as grading
        logger.exception("Converting submission %s failed", submission_id)
        await _mark(session, submission_id, SubmissionStatus.FAILED, f"PDF conversion failed: {exc}")
        return

    if segment_model_id is not None:
        await _suggest(session, submission, segment_model_id)


async def _suggest(session: AsyncSession, submission: Submission, model_id: int | None) -> None:
    """LLM segmentation suggestion. A failure is not fatal: the submission
    still ends up `awaiting_segmentation` (with `error` set) so a professor
    can segment it by hand."""
    if submission.document is None:
        logger.error("Submission %s has no converted document to segment", submission.id)
        return
    submission.status = SubmissionStatus.SEGMENTING
    submission.error = None
    await _commit(session)
    try:
        markdown = storage.download_bytes(submission.document.markdown_object_key).decode("utf-8")
        llm_model = await resolve_llm_model(session, model_id)
        chat_model = get_chat_model(llm_model.provider, llm_model.model, reasoning_effort=llm_model.reasoning_effort)
        segments = await suggest_segments(chat_model, markdown, submission.rubric.questions)
        submission.segments = segments
        submission.segmentation_source = SegmentationSource.LLM
        submission.segmented_at = datetime.now(UTC)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM segmentation of submission %s failed", submission.id)
        submission.error = f"LLM segmentation failed: {exc}"
    submission.status = SubmissionStatus.AWAITING_SEGMENTATION
    await _commit(session)


async def _write_general_feedback(
    submission: Submission, chat_model: BaseChatModel, provider: str, model: str, reasoning_effort: str | None
) -> None:
    """Write the general feedback (`grading/general_feedback.py`) from the
    graded answers -- their effective grade, so a TA override counts. Not
    fatal: on failure the per-question grades stand, the submission still
    ends up `graded`, and `error` says what happened."""
    rubric = submission.rubric
    titles = {q.id: q.title for q in rubric.questions}
    items = [
        GeneralFeedbackItem(title=titles.get(a.question_id, a.question_id), grade=a.effective_grade)
        for a in submission.answers
    ]
    try:
        submission.general_feedback = await write_general_feedback(
            chat_model,
            rubric.general_feedback_instructions or "",
            rubric.grading_scale,
            items,
            rubric_context=rubric.context,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("General feedback of submission %s failed", submission.id)
        submission.error = f"General feedback failed: {exc}"


def extract_submission_file(submission: Submission) -> ExtractionResult:
    """Download the submission's stored file and run its rubric's extractor.
    `submission.rubric` must be loaded. Also used outside grading to get
    images back (they're never stored, see AGENTS.md "Cell outputs and image
    inputs").

    Generic documents (`SEGMENTED_FORMATS`) are sliced from their stored
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
            ExtractedImage(
                mime_type=img.mime_type, data=base64.b64encode(storage.download_bytes(img.object_key)).decode()
            ),
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
    LLM `grade` (or grading `error`) is also carried over for every *other*
    question.

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
                answer.error = prev.error
        answers.append(answer)
    return answers


async def _grade_answers(
    session: AsyncSession,
    submission: Submission,
    questions: list[Question],
    chat_model: BaseChatModel,
    grading_scale: list[GradingLevel],
    provider: str,
    model: str,
    *,
    reasoning_effort: str | None = None,
    only_question_id: str | None = None,
    images: dict[str, list[ExtractedImage]] | None = None,
    rubric_context: str | None = None,
    concurrency: int | None = None,
) -> None:
    """Grade the questions concurrently (at most `concurrency` LLM calls at
    once, default `GRADING_CONCURRENCY`), committing each result as soon as
    it arrives, so polling clients see partial progress.

    Questions are independent: a question whose call raises gets its
    `AnsweredQuestion.error` set (and no grade), and the others carry on.
    The caller decides what that means for the submission. Only the LLM
    calls run concurrently -- every session write and commit happens here,
    one at a time (an `AsyncSession` isn't safe for concurrent use).

    `answers` is a JSONB column, and the grade is written into it in place
    -- `flag_modified` is what makes that in-place change reach the database
    (see `PydanticJSONB`); without it every grade would be silently dropped.

    Also adds a `GradingTrace` row per graded question, captured via
    `grade_answer`'s `on_trace` hook, committed together with that
    question's grade. The trace's compact `info` summary is also stored on
    the answer itself (`AnsweredQuestion.grading_info`) for the submission
    page's tooltip.

    `only_question_id` skips every other question (no LLM call, no trace).
    `images` (`ExtractionResult.images`, by answer key) are attached only for
    questions with `include_images`.
    """
    limit = asyncio.Semaphore(concurrency or get_settings().grading_concurrency)

    async def _grade_one(i: int, question: Question) -> tuple[int, Grade | None, GradeTrace | None, Exception | None]:
        captured_trace: GradeTrace | None = None

        def _capture(trace: GradeTrace) -> None:
            nonlocal captured_trace
            captured_trace = trace

        async with limit:
            try:
                grade = await grade_answer(
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
            except Exception as exc:  # noqa: BLE001 -- recorded on the answer, the other questions go on
                return i, None, None, exc
        return i, grade, captured_trace, None

    pending = [
        asyncio.create_task(_grade_one(i, question))
        for i, question in enumerate(questions)
        if only_question_id is None or question.id == only_question_id
    ]
    try:
        for next_done in asyncio.as_completed(pending):
            i, grade, trace, exc = await next_done
            answer = submission.answers[i]
            if exc is not None:
                logger.error("Grading %s of submission %s failed", questions[i].id, submission.id, exc_info=exc)
                answer.grade = None
                answer.grading_info = None
                answer.error = str(exc) or type(exc).__name__
            else:
                if trace is not None:
                    session.add(GradingTrace(submission_id=submission.id, question_id=questions[i].id, trace=trace))
                answer.grade = grade
                answer.grading_info = trace.info if trace is not None else None
                answer.error = None
            flag_modified(submission, "answers")
            submission.updated_at = datetime.now(UTC)
            await _commit(session)
    finally:
        # Only reached early when a commit failed (e.g. the submission was
        # deleted): don't leave LLM calls running for it.
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
