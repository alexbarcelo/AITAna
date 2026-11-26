"""API request/response shapes. Response models read straight off the ORM
objects (`from_attributes`); nested objects are whatever the endpoint
eager-loaded (`db.models`'s `*_LOADS`)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..db import AnsweredQuestion, BatchType, SegmentationSource, SubmissionStatus
from ..grading.extraction import IMAGE_FORMATS, OUTPUT_FORMATS
from ..grading.models import (
    AnswerSegment,
    ConvertedDocument,
    GeneralFeedback,
    GradeTrace,
    GradingLevel,
    GradingScale,
    Question,
    SubmissionFormat,
    default_grading_scale,
)


class _FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CourseOut(_FromORM):
    id: int
    name: str
    slug: str
    created_at: datetime


class EditionOut(_FromORM):
    id: int
    name: str
    slug: str
    created_at: datetime


class StudentOut(_FromORM):
    id: int
    student_id: str
    name: str
    email: str | None = None
    username: str | None = None
    created_at: datetime


class RubricSummaryOut(_FromORM):
    """A rubric nested inside a submission or batch: everything but its
    course/edition objects (their ids are here)."""

    id: int
    slug: str
    title: str
    course_id: int
    edition_id: int | None
    format: SubmissionFormat
    grading_scale: list[GradingLevel]
    context: str | None = None
    general_feedback_instructions: str | None = None
    questions: list[Question]
    created_at: datetime
    updated_at: datetime


class RubricOut(RubricSummaryOut):
    course: CourseOut
    edition: EditionOut | None


class RubricIn(BaseModel):
    """Body of `POST /rubrics` and `PUT /rubrics/{id}` -- and what the YAML
    upload paths build, so every path validates the same way."""

    title: str
    slug: str | None = None
    course_id: int
    edition_id: int | None = None
    # Fixed per rubric (see `SubmissionFormat`).
    format: SubmissionFormat = SubmissionFormat.PDF_FORM
    grading_scale: GradingScale = Field(default_factory=default_grading_scale)
    # Rubric-wide background shared by every question's grading prompt.
    context: str | None = None
    # Optional: how to digest every question's grade and feedback into one
    # general feedback (grading/general_feedback.py). Unset = no general feedback.
    general_feedback_instructions: str | None = None
    questions: list[Question]

    @model_validator(mode="after")
    def _toggles_supported_by_format(self) -> "RubricIn":
        for q in self.questions:
            if q.include_outputs and self.format not in OUTPUT_FORMATS:
                raise ValueError(
                    f"question {q.id!r}: include_outputs is not supported for format {self.format.value!r}"
                )
            if q.include_images and self.format not in IMAGE_FORMATS:
                raise ValueError(f"question {q.id!r}: include_images is not supported for format {self.format.value!r}")
        return self


class BatchOut(_FromORM):
    id: int
    rubric: RubricSummaryOut
    edition: EditionOut
    type: BatchType
    original_filename: str | None = None
    item_count: int
    created_at: datetime


class SubmissionOut(_FromORM):
    id: int
    student: StudentOut | None
    label: str | None = None
    rubric: RubricSummaryOut
    edition: EditionOut
    batch_id: int | None = None
    batch_internal_id: str | None = None
    file_object_key: str
    original_filename: str | None = None
    original_object_key: str | None = None
    status: SubmissionStatus
    answers: list[AnsweredQuestion]
    general_feedback: GeneralFeedback | None = None
    manual_general_feedback: str | None = None
    manual_general_feedback_at: datetime | None = None
    error: str | None = None
    document: ConvertedDocument | None = None
    segments: list[AnswerSegment] | None = None
    segmentation_source: SegmentationSource | None = None
    segmented_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GradingTraceOut(_FromORM):
    id: int
    submission_id: int
    question_id: str
    trace: GradeTrace
    created_at: datetime
