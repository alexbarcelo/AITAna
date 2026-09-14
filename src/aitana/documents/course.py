from datetime import UTC, datetime

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class Course(Document):
    """A subject, e.g. "BDM" -- reused across years. See Edition for a
    specific term instance of a course (e.g. "2026/27")."""

    name: str
    slug: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "courses"
        indexes = [IndexModel("slug", unique=True)]
