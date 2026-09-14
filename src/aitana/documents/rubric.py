from datetime import UTC, datetime

from beanie import Document, Link
from pydantic import Field, field_validator
from pymongo import IndexModel

from ..grading.models import DEFAULT_GRADING_SCALE, Question, SubmissionFormat
from .course import Course
from .edition import Edition


class Rubric(Document):
    """A gradable deliverable's rubric -- not every deliverable is an exam,
    it might be a lab. "Rubric" covers both."""

    slug: str  # stable id matching the configs/<slug>_questions.yaml filename stem
    title: str
    # Always set: every rubric belongs to a course. Optional edition: a lab
    # is often unchanged between editions (leave unset, reused as-is across
    # years), while an exam is typically unique to one edition (set it).
    course: Link[Course]
    edition: Link[Edition] | None = None
    # Which file format submissions against this rubric are graded from --
    # decided once, at rubric-creation time (there's no rubric edit endpoint
    # yet; see AGENTS.md). Defaults to `pdf` since that's this project's
    # original/only format. A rubric's `questions[].field` values only make
    # sense under this one format (see SubmissionFormat's docstring) -- don't
    # let a submission upload pick a different one per-file.
    format: SubmissionFormat = SubmissionFormat.PDF
    # {level_identifier: description shown to the LLM}, e.g. {"solid": "..."}.
    # Rendered as the prompt's "# Grading" section (see
    # grading/grading.py's build_grading_section) and used to build a
    # per-call structured-output schema restricting the LLM's response to
    # these identifiers (grading/models.py's grade_schema_for_scale) --
    # nothing about the grading levels is hardcoded anymore.
    #
    # Dict order is a load-bearing convention, not incidental: entries run
    # worst -> best. `grade_answer`'s blank-answer short-circuit uses the
    # *first* key verbatim (no LLM call needed to know a blank answer is the
    # worst case), and the frontend's GradeBadge colors levels along a
    # single-hue ramp by this same position. Defaults to
    # `DEFAULT_GRADING_SCALE` (the original 4-level scale) so every rubric
    # created before this existed keeps grading identically; the frontend's
    # rubric-creation form offers a few more presets as a starting point
    # (`frontend/src/api/gradingScalePresets.ts`), but any {id: description}
    # dict is valid -- there's no fixed set of allowed levels.
    grading_scale: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GRADING_SCALE))
    questions: list[Question]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("grading_scale")
    @classmethod
    def _grading_scale_not_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("grading_scale must have at least one level")
        return v

    class Settings:
        name = "rubrics"
        indexes = [IndexModel("slug", unique=True)]
