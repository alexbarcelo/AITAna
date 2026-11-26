"""Grading model selection: which providers this deployment can use, and the
models registered against them (`LlmModel`).

Every endpoint that starts grading (`POST /submissions`, `POST /batches`,
the regrade endpoints, `POST /rubrics/{id}/test-answer`) takes an optional
`?model_id=` -- the frontend's "active model" -- and falls back to the
default model when it's absent. `require_llm_model` is that shared check.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import GLOBAL_USER_KEY, FavoriteLlmModel, LlmModel, LlmModelNotFound, favorite_model_ids, resolve_llm_model
from ...db.session import get_session
from ...grading.llm import PROVIDER_REQUIREMENTS, REASONING_EFFORTS, ReasoningEffort, provider_unavailable_reason
from ...settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

# Every grading-triggering endpoint takes this, as a query parameter (so it
# fits multipart and JSON endpoints alike).
MODEL_ID_QUERY = Query(None, description="LLM model to grade with; defaults to the default model")


class LlmProvider(BaseModel):
    name: str
    available: bool
    # Why it can't be used (package missing, key not set), when it can't.
    unavailable_reason: str | None = None


class LlmModelView(BaseModel):
    """`LlmModel` plus the per-user/per-deployment bits the picker needs, so
    the frontend gets everything in one request."""

    id: int
    provider: str
    model: str
    reasoning_effort: ReasoningEffort | None
    is_default: bool
    is_favorite: bool
    available: bool
    unavailable_reason: str | None = None


class LlmModelCreate(BaseModel):
    provider: str
    model: str
    reasoning_effort: ReasoningEffort | None = None
    is_default: bool = False
    is_favorite: bool = False


class LlmModelUpdate(BaseModel):
    # Required, not defaulted: `null` is a real value here ("provider's own
    # default"), so an omitted field must not silently mean "clear it".
    reasoning_effort: ReasoningEffort | None


async def require_llm_model(session: AsyncSession, model_id: int | None) -> LlmModel:
    """Resolve the model a grading request should use, failing the request
    up front (rather than the background grading job later) when it can't:
    `404` for an unknown `model_id`, `422` when there's no default to fall
    back to or the model's provider isn't usable right now."""
    try:
        model = await resolve_llm_model(session, model_id)
    except LlmModelNotFound as exc:
        raise HTTPException(status_code=404 if model_id is not None else 422, detail=str(exc)) from exc
    if (reason := provider_unavailable_reason(model.provider)) is not None:
        raise HTTPException(status_code=422, detail=f"Model {model.provider}/{model.model} is unavailable: {reason}")
    return model


async def seed_llm_models_from_env(session: AsyncSession) -> None:
    """If no model exists yet and LLM_PROVIDER/LLM_MODEL are set, register
    that pair as the default -- lets a fresh deployment grade without first
    visiting the model picker."""
    settings = get_settings()
    if not (settings.llm_provider and settings.llm_model) or await session.scalar(select(LlmModel.id).limit(1)):
        return
    effort = (settings.llm_reasoning_effort or "").lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        logger.warning("Ignoring LLM_REASONING_EFFORT=%r: must be one of %s", effort, REASONING_EFFORTS)
        effort = None
    session.add(
        LlmModel(
            provider=settings.llm_provider.lower(), model=settings.llm_model, reasoning_effort=effort, is_default=True
        )
    )
    await session.commit()
    logger.info(
        "Seeded default LLM model %s/%s (reasoning_effort=%s) from the environment",
        settings.llm_provider,
        settings.llm_model,
        effort,
    )


async def _get_model(session: AsyncSession, model_id: int) -> LlmModel:
    model = await session.get(LlmModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="LLM model not found")
    return model


async def _make_default(session: AsyncSession, model: LlmModel) -> None:
    """Unset the current default, then set this one -- flushed in that order
    so the single-default partial unique index never sees two at once."""
    await session.execute(update(LlmModel).where(LlmModel.is_default, LlmModel.id != model.id).values(is_default=False))
    model.is_default = True
    await session.flush()


async def _set_favorite(session: AsyncSession, model: LlmModel, favorite: bool) -> None:
    await session.execute(
        delete(FavoriteLlmModel).where(
            FavoriteLlmModel.user_key == GLOBAL_USER_KEY, FavoriteLlmModel.llm_model_id == model.id
        )
    )
    if favorite:
        session.add(FavoriteLlmModel(user_key=GLOBAL_USER_KEY, llm_model_id=model.id))
    await session.flush()


def _view(model: LlmModel, favorite_ids: set[int]) -> LlmModelView:
    reason = provider_unavailable_reason(model.provider)
    return LlmModelView(
        id=model.id,
        provider=model.provider,
        model=model.model,
        reasoning_effort=model.reasoning_effort,
        is_default=model.is_default,
        is_favorite=model.id in favorite_ids,
        available=reason is None,
        unavailable_reason=reason,
    )


async def _commit_and_view(session: AsyncSession, model: LlmModel) -> LlmModelView:
    await session.commit()
    return _view(model, await favorite_model_ids(session))


@router.get("/providers", response_model=list[LlmProvider])
async def list_providers() -> list[LlmProvider]:
    """Every known provider with whether it's usable here (its optional
    package installed and its key/URL in the environment). The frontend only
    offers the available ones when adding a model."""
    providers = []
    for name in PROVIDER_REQUIREMENTS:
        reason = provider_unavailable_reason(name)
        providers.append(LlmProvider(name=name, available=reason is None, unavailable_reason=reason))
    return providers


@router.get("/models", response_model=list[LlmModelView])
async def list_models(session: AsyncSession = Depends(get_session)) -> list[LlmModelView]:
    favorite_ids = await favorite_model_ids(session)
    models = await session.scalars(select(LlmModel).order_by(LlmModel.provider, LlmModel.model))
    return [_view(m, favorite_ids) for m in models]


@router.post("/models", response_model=LlmModelView, status_code=201)
async def create_model(payload: LlmModelCreate, session: AsyncSession = Depends(get_session)) -> LlmModelView:
    """Register a model. `422` if the provider isn't usable here, `409` if
    the same provider/model pair already exists. The first model ever added
    becomes the default regardless of `is_default`, so grading always has
    something to fall back to."""
    provider = payload.provider.lower()
    model_name = payload.model.strip()
    if not model_name:
        raise HTTPException(status_code=422, detail="Model identifier must not be empty")
    if (reason := provider_unavailable_reason(provider)) is not None:
        raise HTTPException(status_code=422, detail=f"Provider {provider!r} is unavailable: {reason}")
    if await session.scalar(select(LlmModel).where(LlmModel.provider == provider, LlmModel.model == model_name)):
        raise HTTPException(status_code=409, detail=f"Model {provider}/{model_name} already exists")

    is_first = not await session.scalar(select(LlmModel.id).limit(1))
    model = LlmModel(provider=provider, model=model_name, reasoning_effort=payload.reasoning_effort)
    session.add(model)
    await session.flush()
    if payload.is_default or is_first:
        await _make_default(session, model)
    if payload.is_favorite:
        await _set_favorite(session, model, True)
    return await _commit_and_view(session, model)


@router.patch("/models/{model_id}", response_model=LlmModelView)
async def update_model(
    model_id: int, payload: LlmModelUpdate, session: AsyncSession = Depends(get_session)
) -> LlmModelView:
    """Change a model's reasoning effort (provider/identifier are its
    identity -- register a new model instead). Takes effect for grading
    enqueued from now on; already-queued jobs re-read the model when they
    run, so they pick it up too."""
    model = await _get_model(session, model_id)
    model.reasoning_effort = payload.reasoning_effort
    return await _commit_and_view(session, model)


@router.put("/models/{model_id}/default", response_model=LlmModelView)
async def set_default_model(model_id: int, session: AsyncSession = Depends(get_session)) -> LlmModelView:
    model = await _get_model(session, model_id)
    await _make_default(session, model)
    return await _commit_and_view(session, model)


@router.put("/models/{model_id}/favorite", response_model=LlmModelView)
async def add_favorite_model(model_id: int, session: AsyncSession = Depends(get_session)) -> LlmModelView:
    model = await _get_model(session, model_id)
    await _set_favorite(session, model, True)
    return await _commit_and_view(session, model)


@router.delete("/models/{model_id}/favorite", response_model=LlmModelView)
async def remove_favorite_model(model_id: int, session: AsyncSession = Depends(get_session)) -> LlmModelView:
    model = await _get_model(session, model_id)
    await _set_favorite(session, model, False)
    return await _commit_and_view(session, model)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """`409` when deleting the default while other models exist -- pick a
    new default first, so grading never silently loses its fallback. The
    last remaining model can always be deleted. Past grades keep their own
    record of which model produced them (`AnsweredQuestion.grading_info`),
    and favorites go with the model (cascading foreign key)."""
    model = await _get_model(session, model_id)
    others = await session.scalar(select(func.count()).select_from(LlmModel).where(LlmModel.id != model.id))
    if model.is_default and others:
        raise HTTPException(status_code=409, detail="Cannot delete the default model -- set another default first")
    await session.delete(model)
    await session.commit()
    return Response(status_code=204)
