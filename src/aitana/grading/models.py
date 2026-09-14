"""Structured output schema returned by the LLM for a single graded answer."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, create_model

# The original, and still default, grading scale: coarse and deliberately
# not numeric -- the point is fast, actionable feedback for the student and
# teacher, not a precise score. A Rubric can override this entirely (see
# Rubric.grading_scale's docstring) -- this is just what a rubric gets when
# it doesn't specify its own, and what the frontend's rubric-creation form
# offers as its default preset (keep frontend/src/api/gradingScalePresets.ts
# in sync if this ever changes).
#
# Dict order matters and is a load-bearing convention, not incidental:
# entries run worst -> best. `grade_answer`'s blank-answer short-circuit
# uses the *first* key verbatim as "no answer was given", and the frontend's
# GradeBadge colors levels along a single-hue ramp by this same position
# (first = lightest/weakest, last = darkest/strongest) -- see its docstring.
DEFAULT_GRADING_SCALE: dict[str, str] = {
    "not_attempted": "Blank, off-topic, or shows no understanding of the question.",
    "some_effort": "On-topic but misses most key points or has major misconceptions.",
    "almost_there": "Covers most key points with minor gaps or imprecision.",
    "solid": "Covers the key points correctly and shows clear understanding.",
}

_FEEDBACK_DESCRIPTION = (
    "Short (2-3 sentence), specific feedback addressed to the student: "
    "what they got right and, more importantly, what they are missing."
)


class Grade(BaseModel):
    """A single graded answer, as stored (`AnsweredQuestion.grade`).

    `level` is deliberately a plain `str`, not an `Enum` -- which level
    identifiers are valid depends on the owning rubric's `grading_scale`
    (arbitrary per-rubric data, not a fixed set known at class-definition
    time). Structural validation against a *specific* rubric's scale happens
    per grading call instead, via `grade_schema_for_scale` below -- this
    class is just the stable shape everything gets normalized back into for
    storage, regardless of which scale produced it.
    """

    level: str = Field(description="Identifier of the assigned grading level (one of the rubric's grading_scale keys).")
    feedback: str = Field(description=_FEEDBACK_DESCRIPTION)


def grade_schema_for_scale(grading_scale: dict[str, str]) -> type[BaseModel]:
    """Build a one-off structured-output schema for one grading call.

    `Grade.level` can't statically enumerate valid values (they're per-rubric
    data), but the LLM tool-calling schema passed to
    `with_structured_output()` should still constrain the model to *this*
    rubric's actual level identifiers rather than accepting any string --
    otherwise a model could return a level nobody configured. Built fresh per
    `grade_answer()` call rather than cached: it's cheap (one `create_model`
    call) and rubrics aren't typically graded back-to-back with different
    scales in a way that would make caching worthwhile.
    """
    levels = tuple(grading_scale.keys())
    return create_model(
        "GradeOutput",
        level=(Literal[levels], Field(description=f"Exactly one of: {', '.join(levels)}.")),
        feedback=(str, Field(description=_FEEDBACK_DESCRIPTION)),
    )


class SubmissionFormat(str, Enum):
    """Which file format a rubric's submissions are graded from.

    Set once on the `Rubric` (see `documents/rubric.py`), not per
    `Submission`: a rubric's `Question.field` values only make sense under
    one format (a PDF AcroForm field name vs. a notebook cell tag), so a
    rubric authored for one format never accepts the other. Determines two
    things: which extractor in `grading/extraction/` turns the raw uploaded
    file into `{answer_key: text}`, and how `Question.field` is interpreted
    while doing that.
    """

    PDF = "pdf"
    NOTEBOOK = "notebook"


class Question(BaseModel):
    """One gradable question: where to read the student's answer from the
    submitted file, plus grading instructions.

    DB-agnostic on purpose -- reused both as the grading-time input (see
    grading.py) and embedded verbatim inside the `Rubric` Beanie document.
    """

    id: str
    # Identifies where in the submitted file this question's answer lives --
    # meaning depends on the owning Rubric's `format` (see SubmissionFormat):
    # a PDF AcroForm text-field name for `pdf` (e.g. "answer1"), or the
    # value content of an `aitana/id` cell's metadata for `notebook`.
    # Defaults to `id` since both conventions typically name these answer1,
    # answer2, etc., lining up 1:1 with `id`.
    field: str | None = None
    title: str
    # The actual question/task text posed to the student -- distinct from
    # `title` (a short label, e.g. for UI headers) and from `rubric` (grading
    # criteria, not what was asked).
    question: str
    # Optional environment/state description -- for a lab, what the student's
    # environment looked like at this point (prior steps already run, what
    # exists already, etc.), so the rubric can be understood without also
    # having the full lab guide on hand.
    context: str | None = None
    rubric: str
    expected_points: list[str] = Field(default_factory=list)

    @property
    def answer_key(self) -> str:
        return self.field or self.id
