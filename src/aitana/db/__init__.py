"""PostgreSQL persistence: ORM models (`models.py`), engine/session setup
(`session.py`) and the small query helpers shared by the API and the
worker."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    BATCH_LOADS,
    GLOBAL_USER_KEY,
    RUBRIC_LOADS,
    SUBMISSION_LOADS,
    AnsweredQuestion,
    Base,
    Batch,
    BatchType,
    Course,
    Edition,
    FavoriteLlmModel,
    GradingTrace,
    LlmModel,
    Rubric,
    SegmentationSource,
    Student,
    Submission,
    SubmissionStatus,
    utcnow,
)


class LlmModelNotFound(LookupError):
    pass


async def resolve_llm_model(session: AsyncSession, model_id: int | None) -> LlmModel:
    """The model `model_id` names, or the default one when it's `None`.
    Raises `LlmModelNotFound` (with a message fit for `Submission.error` / an
    HTTP detail) if neither resolves."""
    if model_id is not None:
        model = await session.get(LlmModel, model_id)
        if model is None:
            raise LlmModelNotFound(f"LLM model {model_id} not found")
        return model
    model = await session.scalar(select(LlmModel).where(LlmModel.is_default))
    if model is None:
        raise LlmModelNotFound("No default LLM model is configured -- add one from the model picker")
    return model


async def favorite_model_ids(session: AsyncSession, user_key: str = GLOBAL_USER_KEY) -> set[int]:
    rows = await session.scalars(select(FavoriteLlmModel.llm_model_id).where(FavoriteLlmModel.user_key == user_key))
    return set(rows)


__all__ = [
    "BATCH_LOADS",
    "GLOBAL_USER_KEY",
    "RUBRIC_LOADS",
    "SUBMISSION_LOADS",
    "AnsweredQuestion",
    "Base",
    "Batch",
    "BatchType",
    "Course",
    "Edition",
    "FavoriteLlmModel",
    "GradingTrace",
    "LlmModel",
    "LlmModelNotFound",
    "Rubric",
    "SegmentationSource",
    "Student",
    "Submission",
    "SubmissionStatus",
    "favorite_model_ids",
    "resolve_llm_model",
    "utcnow",
]
