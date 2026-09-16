from datetime import UTC, datetime
from enum import Enum

from beanie import Document, Link
from pydantic import BaseModel, Field
from pymongo import IndexModel

from ..grading.models import Grade
from .batch import Batch
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
    every rubric is an exam, so "submission" fits both labs and exams.

    Normally one per student x rubric -- but a submission created by a batch
    upload (see `batch`/`batch_internal_id` below) starts out with no
    `student`: the zip's per-item folder name isn't necessarily identifiable
    against our roster at upload time, only `rubric`/`edition` are known.
    Grading doesn't need `student` at all (see `worker/tasks.py`), so this
    doesn't block a batch item from being graded -- only from being
    attributed to a specific student until something matches it up later."""

    student: Link[Student] | None = None
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
    # Set only for a submission created by a batch upload -- which batch it
    # came from, and the exact per-item folder name from that batch's zip
    # (e.g., for an Atenea batch, `<surname(s)> <name>_<internal_id>_
    # assignsubmission_file`), kept verbatim rather than parsed apart. A
    # later feedback-export feature uses this id to write grades back into
    # the originating LMS against the same item -- not implemented yet, but
    # this is where it reads that id from.
    batch: Link[Batch] | None = None
    batch_internal_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "submissions"
        indexes = [IndexModel("status")]
