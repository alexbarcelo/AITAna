from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

# There are no user accounts yet: every setting lives in this one document.
# `key` is what a future per-user migration keys by (a user id), so readers
# go through `get_user_settings()` rather than assuming a singleton.
GLOBAL_USER_SETTINGS_KEY = "global"


class UserSettings(Document):
    """Per-user preferences (today: a single global one). Only things that
    are genuinely a matter of taste belong here -- e.g. favorite models --
    not app-wide config like the default model (`LlmModel.is_default`)."""

    key: str = GLOBAL_USER_SETTINGS_KEY
    favorite_model_ids: list[PydanticObjectId] = Field(default_factory=list)

    class Settings:
        name = "user_settings"
        indexes = [IndexModel("key", unique=True)]


async def get_user_settings() -> UserSettings:
    """The current user's settings, created empty on first access."""
    settings = await UserSettings.find_one(UserSettings.key == GLOBAL_USER_SETTINGS_KEY)
    if settings is None:
        settings = UserSettings()
        await settings.insert()
    return settings
