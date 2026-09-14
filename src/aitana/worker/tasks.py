"""Celery task: extract answers from a submission's raw file and grade each question.

Beanie/Motor are async; Celery tasks run sync, so each task wraps its DB work
in asyncio.run(...) and initializes Beanie fresh (a new event loop needs its
own Motor client) -- fine for the one-shot, short-lived nature of this task.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from langchain_core.language_models.chat_models import BaseChatModel

from .. import storage
from ..db import init_db
from ..documents import AnsweredQuestion, Submission, SubmissionStatus
from ..grading.extraction import FORMAT_FILE_INFO, extract_answers
from ..grading.grading import grade_answer
from ..grading.llm import get_chat_model
from ..grading.models import Question
from ..settings import get_settings
from .celery_app import celery_app

logger = logging.getLogger(__name__)

# See api/routers/submissions.py's _SHALLOW_LINKS -- grading only needs
# rubric.questions, never rubric.course/rubric.edition.
_SHALLOW_RUBRIC_LINK = {"rubric": 1}


@celery_app.task(name="aitana.grade_submission")
def grade_submission(submission_id: str) -> None:
    asyncio.run(_grade_submission(submission_id))


async def _grade_submission(submission_id: str) -> None:
    await init_db()
    submission = await Submission.get(
        PydanticObjectId(submission_id), fetch_links=True, nesting_depths_per_field=_SHALLOW_RUBRIC_LINK
    )
    if submission is None:
        logger.error("Submission %s not found", submission_id)
        return

    try:
        submission.status = SubmissionStatus.EXTRACTING
        await submission.save()

        rubric = submission.rubric
        extension, _content_type = FORMAT_FILE_INFO[rubric.format]
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / f"submission{extension}"
            storage.download_to_path(submission.file_object_key, file_path)
            extracted = extract_answers(rubric.format, file_path)

        submission.status = SubmissionStatus.GRADING
        submission.answers = [
            AnsweredQuestion(question_id=q.id, student_answer=extracted.get(q.answer_key, ""))
            for q in rubric.questions
        ]
        await submission.save()

        settings = get_settings()
        chat_model = get_chat_model(settings.llm_provider, settings.llm_model)
        await _grade_answers(submission, rubric.questions, chat_model, rubric.grading_scale)

        submission.status = SubmissionStatus.GRADED
        await submission.save()
    except Exception as exc:  # noqa: BLE001 -- one bad submission shouldn't crash the worker
        logger.exception("Grading submission %s failed", submission_id)
        submission.status = SubmissionStatus.FAILED
        submission.error = str(exc)
        await submission.save()


async def _grade_answers(
    submission: Submission, questions: list[Question], chat_model: BaseChatModel, grading_scale: dict[str, str]
) -> None:
    """Grade each question in order, saving after every single one.

    Re-indexes into `submission.answers[i]` fresh each iteration rather than
    holding object references from a single zip() bound before the loop
    started: `Document.save()` re-parses the whole document and replaces
    `answers` with newly-constructed `AnsweredQuestion` instances every time
    (Beanie's `validate_on_save`), so a reference captured before any
    `save()` call goes stale after the first one -- mutating it is a no-op
    that silently drops every grade past the first question.
    """
    for i, question in enumerate(questions):
        grade = grade_answer(chat_model, question, submission.answers[i].student_answer, grading_scale)
        submission.answers[i].grade = grade
        await submission.save()
