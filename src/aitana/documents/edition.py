from datetime import UTC, datetime

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class Edition(Document):
    """A term, e.g. "2026/27" -- global, shared across every course taught
    that term, not owned by any one of them. Which course(s) an edition
    actually applies to is expressed elsewhere, per use: a `Rubric` pins
    itself to one course *and* (optionally) one edition (`Rubric.course`/
    `.edition`), and a `Student` enrolls directly in editions
    (`Student.edition_ids`) -- there is no `Edition.course` here to look
    either of those up through. See AGENTS.md's "Data model" section for why
    this was pulled out of `Edition` (it used to belong to exactly one
    course, which meant "2026/27" had to be recreated once per course and
    then looked like two unrelated, identically-named editions everywhere in
    the UI)."""

    name: str
    slug: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "editions"
        indexes = [IndexModel("slug", unique=True)]
