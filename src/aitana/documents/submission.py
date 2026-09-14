from datetime import UTC, datetime
from enum import Enum

from beanie import Document, Link
from pydantic import BaseModel, Field
from pymongo import IndexModel

from ..grading.models import Grade
from .edition import Edition
from .rubric import Rubric
from .student import Student


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    GRADING = "grading"
    GRADED = "graded"
    FAILED = "failed"


class AnsweredQuestion(BaseModel):
    question_id: str
    student_answer: str
    grade: Grade | None = None


class Submission(Document):
    """A student's deliverable against a rubric -- "correction" was a
    confusing name (it reads as "the fix," not "the thing graded"), and not
    every rubric is an exam, so "submission" fits both labs and exams."""

    student: Link[Student]
    rubric: Link[Rubric]
    # Always set, even when the rubric itself has no fixed edition: a
    # submission always happens within some specific term, and this is the
    # only place that's recorded for rubrics that are edition-agnostic (e.g.
    # a lab). Populated from `rubric.edition` if the rubric has one, else
    # from the uploader's explicit choice -- see api/routers/submissions.py.
    edition: Link[Edition]
    # Raw uploaded file (PDF, notebook, ...) as stored in MinIO -- its format
    # is whatever `rubric.format` says, not recorded again here (see
    # Rubric.format's docstring for why format lives on the rubric, not the
    # submission).
    file_object_key: str
    status: SubmissionStatus = SubmissionStatus.PENDING
    answers: list[AnsweredQuestion] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "submissions"
        indexes = [IndexModel("status")]
