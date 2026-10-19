"""Grading model selection: which providers this deployment can use, and the
models registered against them (see documents/llm_model.py).

Every endpoint that starts grading (`POST /submissions`, `POST /batches`,
the regrade endpoints, `POST /rubrics/{id}/test-answer`) takes an optional
`?model_id=` -- the frontend's "active model" -- and falls back to the
default model when it's absent. `require_llm_model` is that shared check.
"""

import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ...documents import LlmModel, LlmModelNotFound, get_user_settings, resolve_llm_model
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

    id: PydanticObjectId
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


async def require_llm_model(model_id: PydanticObjectId | None) -> LlmModel:
    """Resolve the model a grading request should use, failing the request
    up front (rather than the background grading job later) when it can't:
    `404` for an unknown `model_id`, `422` when there's no default to fall
    back to or the model's provider isn't usable right now."""
    try:
        model = await resolve_llm_model(model_id)
    except LlmModelNotFound as exc:
        raise HTTPException(status_code=404 if model_id is not None else 422, detail=str(exc)) from exc
    if (reason := provider_unavailable_reason(model.provider)) is not None:
        raise HTTPException(status_code=422, detail=f"Model {model.provider}/{model.model} is unavailable: {reason}")
    return model


async def seed_llm_models_from_env() -> None:
    """Upgrade path from the env-only config: if no model exists yet and
    LLM_PROVIDER/LLM_MODEL are set, register that pair as the default."""
    settings = get_settings()
    if not (settings.llm_provider and settings.llm_model) or await LlmModel.find_one() is not None:
        return
    effort = (settings.llm_reasoning_effort or "").lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        logger.warning("Ignoring LLM_REASONING_EFFORT=%r: must be one of %s", effort, REASONING_EFFORTS)
        effort = None
    await LlmModel(
        provider=settings.llm_provider.lower(), model=settings.llm_model, reasoning_effort=effort, is_default=True
    ).insert()
    logger.info(
        "Seeded default LLM model %s/%s (reasoning_effort=%s) from the environment",
        settings.llm_provider,
        settings.llm_model,
        effort,
    )


async def _get_model(model_id: PydanticObjectId) -> LlmModel:
    model = await LlmModel.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="LLM model not found")
    return model


async def _make_default(model: LlmModel) -> None:
    # Two writes, not atomic -- fine for a single-admin settings toggle; the
    # worst a race could leave is two defaults, and the next toggle fixes it.
    await LlmModel.find(LlmModel.is_default == True, LlmModel.id != model.id).update_many(  # noqa: E712
        {"$set": {"is_default": False}}
    )
    model.is_default = True
    await model.save()


def _view(model: LlmModel, favorite_ids: set[PydanticObjectId]) -> LlmModelView:
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
async def list_models() -> list[LlmModelView]:
    favorite_ids = set((await get_user_settings()).favorite_model_ids)
    models = await LlmModel.find_all().sort(LlmModel.provider, LlmModel.model).to_list()
    return [_view(m, favorite_ids) for m in models]


@router.post("/models", response_model=LlmModelView, status_code=201)
async def create_model(payload: LlmModelCreate) -> LlmModelView:
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
    if await LlmModel.find_one(LlmModel.provider == provider, LlmModel.model == model_name):
        raise HTTPException(status_code=409, detail=f"Model {provider}/{model_name} already exists")

    is_first = await LlmModel.find_one() is None
    model = LlmModel(provider=provider, model=model_name, reasoning_effort=payload.reasoning_effort)
    await model.insert()
    if payload.is_default or is_first:
        await _make_default(model)

    user_settings = await get_user_settings()
    if payload.is_favorite:
        user_settings.favorite_model_ids.append(model.id)
        await user_settings.save()
    return _view(model, set(user_settings.favorite_model_ids))


@router.patch("/models/{model_id}", response_model=LlmModelView)
async def update_model(model_id: PydanticObjectId, payload: LlmModelUpdate) -> LlmModelView:
    """Change a model's reasoning effort (provider/identifier are its
    identity -- register a new model instead). Takes effect for grading
    enqueued from now on; already-queued jobs re-read the model when they
    run, so they pick it up too."""
    model = await _get_model(model_id)
    model.reasoning_effort = payload.reasoning_effort
    await model.save()
    return _view(model, set((await get_user_settings()).favorite_model_ids))


@router.put("/models/{model_id}/default", response_model=LlmModelView)
async def set_default_model(model_id: PydanticObjectId) -> LlmModelView:
    model = await _get_model(model_id)
    await _make_default(model)
    return _view(model, set((await get_user_settings()).favorite_model_ids))


@router.put("/models/{model_id}/favorite", response_model=LlmModelView)
async def add_favorite_model(model_id: PydanticObjectId) -> LlmModelView:
    model = await _get_model(model_id)
    user_settings = await get_user_settings()
    if model.id not in user_settings.favorite_model_ids:
        user_settings.favorite_model_ids.append(model.id)
        await user_settings.save()
    return _view(model, set(user_settings.favorite_model_ids))


@router.delete("/models/{model_id}/favorite", response_model=LlmModelView)
async def remove_favorite_model(model_id: PydanticObjectId) -> LlmModelView:
    model = await _get_model(model_id)
    user_settings = await get_user_settings()
    if model.id in user_settings.favorite_model_ids:
        user_settings.favorite_model_ids.remove(model.id)
        await user_settings.save()
    return _view(model, set(user_settings.favorite_model_ids))


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: PydanticObjectId) -> Response:
    """`409` when deleting the default while other models exist -- pick a
    new default first, so grading never silently loses its fallback. The
    last remaining model can always be deleted. Past grades keep their own
    record of which model produced them (`AnsweredQuestion.grading_info`),
    so nothing else references a model."""
    model = await _get_model(model_id)
    if model.is_default and await LlmModel.find(LlmModel.id != model.id).count() > 0:
        raise HTTPException(status_code=409, detail="Cannot delete the default model -- set another default first")
    await model.delete()
    user_settings = await get_user_settings()
    if model_id in user_settings.favorite_model_ids:
        user_settings.favorite_model_ids.remove(model_id)
        await user_settings.save()
    return Response(status_code=204)
