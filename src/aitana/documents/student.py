from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
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
    # Plain Edition ids, not Beanie Links: a student can be in many editions,
    # and Beanie's Link-list querying doesn't have the same query-sugar
    # support as a single Link field (see AGENTS.md) -- a plain array plays
    # much more simply with Mongo's native "array contains" equality query.
    # Enrollment only, nothing more -- editions are global (no Edition.course
    # to look a course up through anymore, see documents/edition.py), so
    # which course(s) this enrollment relates to isn't derivable from
    # edition_ids alone. This was never consulted for filtering submissions
    # anyway (see AGENTS.md's "Creating courses and editions").
    edition_ids: list[PydanticObjectId] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "students"
        indexes = [IndexModel("student_id", unique=True)]
