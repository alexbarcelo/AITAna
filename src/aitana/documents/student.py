from datetime import UTC, datetime

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class Student(Document):
    student_id: str  # institutional id, e.g. university NIU -- unique roster key
    name: str
    email: str | None = None
    # Login/username from a roster export (e.g. Atenea's Moodle username) --
    # purely informational metadata, not used as a lookup key anywhere
    # (student_id fills that role). See "Batch student import" in AGENTS.md.
    username: str | None = None
    # Free-form roster grouping (e.g. a lab/seminar group from a roster
    # export) -- purely informational metadata at the moment, not wired into
    # editions/rubrics/submissions filtering. See "Batch student import"
    # in AGENTS.md for where this is populated from.
    group: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "students"
        indexes = [IndexModel("student_id", unique=True)]
