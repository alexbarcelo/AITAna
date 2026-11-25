"""ORM models (SQLAlchemy 2.0, PostgreSQL).

Relations are real foreign keys; nested, per-row data that is always read
and written as a whole (a rubric's questions, a submission's answers, a
grading trace) lives in JSONB columns typed by the DB-agnostic Pydantic
models in `grading/models.py` (`PydanticJSONB`).

Every relationship is `lazy="raise"`: nothing is ever loaded implicitly
(which async SQLAlchemy can't do anyway). A query states what it needs with
`selectinload(...)` -- see the `*_LOADS` tuples below for the shapes the
API serializes.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

from ..grading.models import (
    AnswerSegment,
    ConvertedDocument,
    Grade,
    GradeTrace,
    GradingLevel,
    Question,
    SubmissionFormat,
    default_grading_scale,
)
from ..slugify import slugify
from .types import PydanticJSONB


def utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum(enum_cls: type[Enum], length: int = 32) -> SAEnum:
    """Stored as its plain string value in a VARCHAR (no native PG enum, no
    CHECK constraint): adding a member never needs a schema migration."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    def __init__(self, **kwargs: Any) -> None:
        """Fill every column the caller didn't pass with its Python default
        (or `None`), right away rather than at INSERT time. A column left
        unset would otherwise be loaded lazily on first access after the
        flush, which async SQLAlchemy can't do (it raises)."""
        for attr in sa_inspect(type(self)).column_attrs:
            column = attr.columns[0]
            if attr.key in kwargs or column.primary_key:
                continue
            default = column.default
            if default is None:
                kwargs[attr.key] = None
            elif default.is_callable:
                kwargs[attr.key] = default.arg(None)
            elif default.is_scalar:
                kwargs[attr.key] = default.arg
        cls = type(self)
        for key, value in kwargs.items():
            if not hasattr(cls, key):
                raise TypeError(f"{key!r} is an invalid keyword argument for {cls.__name__}")
            setattr(self, key, value)


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Course(Base):
    """A subject, e.g. "BDM" -- reused across years (an `Edition` is the
    term)."""

    __tablename__ = "courses"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()


class Edition(Base):
    """A term, e.g. "2026/27" -- global, shared across every course taught
    that term. Which courses an edition applies to is derived from rubrics
    (`Rubric.course` + `Rubric.edition`), never stored."""

    __tablename__ = "editions"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = _pk()
    # Institutional id (e.g. a university NIU) -- the unique roster key.
    student_id: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    # Login/username from a roster export -- informational only, never a
    # lookup key.
    username: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Rubric(Base):
    """One lab or exam: its questions, file format and grading scale.

    `format` is fixed per rubric (see `SubmissionFormat`), and can't change
    once submissions exist (`api/routers/rubrics.py`'s
    `_guard_format_change`). `grading_scale` must satisfy `GradingScale`'s
    rules -- validated by the API schemas, not here."""

    __tablename__ = "rubrics"

    id: Mapped[int] = _pk()
    slug: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="RESTRICT"), index=True)
    # Optional: a lab is usually reused unchanged across editions (left
    # unset); an exam usually belongs to one (set).
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="RESTRICT"), index=True)
    format: Mapped[SubmissionFormat] = mapped_column(_str_enum(SubmissionFormat), default=SubmissionFormat.PDF_FORM)
    grading_scale: Mapped[list[GradingLevel]] = mapped_column(
        PydanticJSONB(list[GradingLevel]), default=default_grading_scale
    )
    # Rubric-wide background shared by every question's grading prompt.
    context: Mapped[str | None] = mapped_column(Text)
    questions: Mapped[list[Question]] = mapped_column(PydanticJSONB(list[Question]), default=list)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(lazy="raise")
    edition: Mapped[Edition | None] = relationship(lazy="raise")


class BatchType(str, Enum):
    """Which zip layout a batch upload follows. Add a member plus a parser
    registered in `api/routers/batches.py`'s `_BATCH_PARSERS` for another."""

    ATENEA = "atenea"


class Batch(Base):
    """One zip upload: one submission per subfolder, all against the same
    rubric and edition (resolved like a single submission's edition)."""

    __tablename__ = "batches"

    id: Mapped[int] = _pk()
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id", ondelete="RESTRICT"), index=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="RESTRICT"), index=True)
    type: Mapped[BatchType] = mapped_column(_str_enum(BatchType))
    original_filename: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = _created_at()

    rubric: Mapped[Rubric] = relationship(lazy="raise")
    edition: Mapped[Edition] = relationship(lazy="raise")


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    # Generic documents only (`SEGMENTED_FORMATS`): conversion to Markdown,
    # then an optional LLM segmentation suggestion, then waiting for a
    # professor to confirm the segments -- see AGENTS.md "Generic-document
    # submissions".
    CONVERTING = "converting"
    SEGMENTING = "segmenting"
    AWAITING_SEGMENTATION = "awaiting_segmentation"
    EXTRACTING = "extracting"
    GRADING = "grading"
    GRADED = "graded"
    FAILED = "failed"


class SegmentationSource(str, Enum):
    LLM = "llm"
    MANUAL = "manual"


class AnsweredQuestion(BaseModel):
    """One question's answer within a submission (`Submission.answers`)."""

    question_id: str
    student_answer: str
    # Cells of `student_answer` the extractor picked up *without* an explicit
    # answer tag (grading/extraction/notebook.py's "Untagged cells").
    # Informational, so a TA knows to double-check; always 0 for formats
    # with no such heuristic.
    untagged_cells: int = 0
    # Images attached to this answer's grading call (Question.include_images);
    # the images themselves are re-extracted from the file on demand.
    image_count: int = 0
    # The LLM's grade, exactly as `grade_answer` produced it -- never edited
    # by hand. A TA's correction goes in `manual_grade`, so both stay
    # visible, and a re-grade refreshes this one while the override survives
    # (worker/tasks.py's `_rebuild_answers`).
    grade: Grade | None = None
    # Compact audit info about the LLM call behind `grade` (grading.py's
    # `build_grading_info`). Display-only, keys vary by provider.
    grading_info: dict[str, Any] | None = None
    manual_grade: Grade | None = None
    manual_graded_at: datetime | None = None

    @property
    def effective_grade(self) -> Grade | None:
        """What the student should see: the TA's override if any, else the
        LLM's grade."""
        return self.manual_grade or self.grade


class Submission(Base):
    """A deliverable against a rubric. Attributed to a roster `student`, or
    identified by a free-form `label` (e.g. a group project), or neither yet
    (a batch item not matched to a student) -- grading needs none of them.

    `answers` is a JSONB column: after mutating it in place, call
    `flag_modified(submission, "answers")` (see `PydanticJSONB`)."""

    __tablename__ = "submissions"
    __table_args__ = (Index("ix_submissions_status", "status"),)

    id: Mapped[int] = _pk()
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    # Display-only identifier for a submission not attributed to a roster
    # student; never a lookup key, not unique.
    label: Mapped[str | None] = mapped_column(Text)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id", ondelete="RESTRICT"), index=True)
    # Always set, even when the rubric has no fixed edition: the rubric's
    # own edition if it has one, else the uploader's explicit choice.
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="RESTRICT"), index=True)
    # The stored file (PDF, notebook, ...) in object storage; its format is
    # `rubric.format`.
    file_object_key: Mapped[str] = mapped_column(Text, default="")
    # The uploaded file's name (for a batch item, its name inside the zip
    # folder).
    original_filename: Mapped[str | None] = mapped_column(Text)
    # Set only when the upload was converted before storing (e.g. `.docx` ->
    # PDF): the untouched uploaded bytes, next to the stored file.
    original_object_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SubmissionStatus] = mapped_column(_str_enum(SubmissionStatus), default=SubmissionStatus.PENDING)
    answers: Mapped[list[AnsweredQuestion]] = mapped_column(PydanticJSONB(list[AnsweredQuestion]), default=list)
    error: Mapped[str | None] = mapped_column(Text)
    # Batch-created submissions only: the batch, and the item's folder name
    # from the zip kept verbatim -- the feedback zip writes each file back
    # under that same folder name.
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="RESTRICT"), index=True)
    batch_internal_id: Mapped[str | None] = mapped_column(Text)
    # Generic documents only: conversion metadata (the Markdown and images
    # are in object storage), and where each answer lives in that Markdown.
    document: Mapped[ConvertedDocument | None] = mapped_column(PydanticJSONB(ConvertedDocument))
    segments: Mapped[list[AnswerSegment] | None] = mapped_column(PydanticJSONB(list[AnswerSegment]))
    segmentation_source: Mapped[SegmentationSource | None] = mapped_column(_str_enum(SegmentationSource))
    segmented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    student: Mapped[Student | None] = relationship(lazy="raise")
    rubric: Mapped[Rubric] = relationship(lazy="raise")
    edition: Mapped[Edition] = relationship(lazy="raise")
    batch: Mapped[Batch | None] = relationship(lazy="raise")

    @property
    def display_name(self) -> str:
        """Who/what this submission is, for humans: the student's name, else
        the `label`, else the batch folder name, else the id. Needs
        `student` loaded."""
        if self.student is not None:
            return self.student.name
        return self.label or self.batch_internal_id or str(self.id)

    @property
    def file_identifier(self) -> str:
        """Same fallback chain as `display_name`, but filename-safe and
        starting from the institutional `student_id` -- for download
        filenames."""
        if self.student is not None:
            return self.student.student_id
        if self.label:
            return slugify(self.label)
        return self.batch_internal_id or str(self.id)


class GradingTrace(Base):
    """One row per real `grade_answer()` call made by the worker: the full
    prompt, any thinking/tool-call content, token usage (`GradeTrace`).
    Kept out of `submissions` so this bulky diagnostic data never rides
    along with a submission. No expiry: pruned explicitly through
    `DELETE /grading-traces`. Re-grades add new traces next to the old ones."""

    __tablename__ = "grading_traces"
    __table_args__ = (Index("ix_grading_traces_submission_question", "submission_id", "question_id"),)

    id: Mapped[int] = _pk()
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(Text)
    trace: Mapped[GradeTrace] = mapped_column(PydanticJSONB(GradeTrace))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LlmModel(Base):
    """A grading model registered from the UI: a provider plus that
    provider's identifier for the model. No credentials here -- API keys and
    URLs stay in the environment (`grading/llm.py`'s
    `PROVIDER_REQUIREMENTS`). At most one row has `is_default` (enforced by a
    partial unique index)."""

    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint("provider", "model", name="uq_llm_models_provider_model"),
        Index("uq_llm_models_single_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )

    id: Mapped[int] = _pk()
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(Text)
    # "low"/"medium"/"high", or NULL for the provider's own default.
    reasoning_effort: Mapped[str | None] = mapped_column(String(16))
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created_at()


# There are no user accounts yet: every preference belongs to this one key.
# A future per-user version keys by user id instead.
GLOBAL_USER_KEY = "global"


class FavoriteLlmModel(Base):
    """A user's favorite grading model (the model picker lists favorites
    first). Deleting a model removes it from every favorites list."""

    __tablename__ = "favorite_llm_models"

    user_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    llm_model_id: Mapped[int] = mapped_column(ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True)


# Eager-load shapes matching the API response schemas (`api/schemas.py`).
RUBRIC_LOADS = (selectinload(Rubric.course), selectinload(Rubric.edition))
BATCH_LOADS = (selectinload(Batch.rubric), selectinload(Batch.edition))
SUBMISSION_LOADS = (
    selectinload(Submission.student),
    selectinload(Submission.rubric),
    selectinload(Submission.edition),
)

__all__ = [
    "AnsweredQuestion",
    "BATCH_LOADS",
    "Base",
    "Batch",
    "BatchType",
    "Course",
    "Edition",
    "FavoriteLlmModel",
    "GLOBAL_USER_KEY",
    "GradingTrace",
    "LlmModel",
    "RUBRIC_LOADS",
    "Rubric",
    "SUBMISSION_LOADS",
    "SegmentationSource",
    "Student",
    "Submission",
    "SubmissionStatus",
    "utcnow",
]
