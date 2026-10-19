from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from ..grading.llm import ReasoningEffort


class LlmModel(Document):
    """A grading model registered from the UI: a provider plus the
    identifier that provider uses for the model (`gpt-4o-mini`,
    `claude-sonnet-5`, `z-ai/glm-4.5`, ...). No credentials here -- API
    keys/URLs stay in the environment (see grading/llm.py's
    `PROVIDER_REQUIREMENTS`), so a model whose provider later loses its key
    stays listed, just unavailable.

    At most one model has `is_default` set (enforced by the `/llm` router,
    not the database): it's what grading uses when the request names no
    model. Favorites are not stored here but in `UserSettings`, since they're
    meant to become per-user.
    """

    provider: str
    model: str
    # "How hard should it think" (see grading/llm.py's get_chat_model for
    # what each level means per provider); `None` leaves the provider's own
    # default untouched. A setting of the model, not part of its identity:
    # editable after creation, and not in the unique index.
    reasoning_effort: ReasoningEffort | None = None
    is_default: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "llm_models"
        indexes = [IndexModel([("provider", 1), ("model", 1)], unique=True)]


class LlmModelNotFound(LookupError):
    pass


async def resolve_llm_model(model_id: PydanticObjectId | str | None) -> LlmModel:
    """The model `model_id` names, or the default one when it's `None`.
    Raises `LlmModelNotFound` (with a message fit for `Submission.error` / an
    HTTP detail) if neither resolves."""
    if model_id is not None:
        model = await LlmModel.get(PydanticObjectId(model_id))
        if model is None:
            raise LlmModelNotFound(f"LLM model {model_id} not found")
        return model
    model = await LlmModel.find_one(LlmModel.is_default == True)  # noqa: E712 -- Beanie query, not a Python comparison
    if model is None:
        raise LlmModelNotFound("No default LLM model is configured -- add one from the model picker")
    return model
