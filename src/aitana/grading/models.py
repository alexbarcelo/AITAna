"""Structured output schema returned by the LLM for a single graded answer."""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, create_model

class GradingLevel(BaseModel):
    """One level of a rubric's grading scale. `id` is what the LLM answers
    with and what's stored on a `Grade`; `description` is what the LLM grades
    against; `score` is the level's numeric value, used for ordering and for
    the grading statistics (see `GradingScale`)."""

    id: str = Field(min_length=1)
    description: str
    score: float


def _check_grading_scale(levels: list[GradingLevel]) -> list[GradingLevel]:
    if not levels:
        raise ValueError("grading_scale must have at least one level")
    ids = [level.id for level in levels]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"grading_scale has duplicate level ids: {', '.join(duplicates)}")
    zeros = [level.id for level in levels if level.score == 0]
    if len(zeros) != 1:
        raise ValueError(
            "grading_scale must have exactly one level with score 0 (the level a blank answer gets)"
            + (f", found {len(zeros)}: {', '.join(zeros)}" if zeros else "")
        )
    return levels


# A rubric's grading scale: a list of levels with distinct ids and exactly
# one level scored 0 -- the level a blank answer gets without calling the
# LLM (`blank_level`). Levels are ordered by `score`, never by list
# position: worst = lowest score (`ordered_levels`).
GradingScale = Annotated[list[GradingLevel], AfterValidator(_check_grading_scale)]


def ordered_levels(grading_scale: list[GradingLevel]) -> list[GradingLevel]:
    """Levels from worst to best (ascending score; ties keep list order)."""
    return sorted(grading_scale, key=lambda level: level.score)


def blank_level(grading_scale: list[GradingLevel]) -> GradingLevel:
    """The level a blank answer gets: the one scored 0 (`GradingScale`
    guarantees there is exactly one)."""
    return next(level for level in grading_scale if level.score == 0)


# What a rubric gets when it doesn't specify its own scale. The frontend's
# rubric form offers the same levels as its default preset
# (frontend/src/api/gradingScalePresets.ts) -- keep both in sync.
DEFAULT_GRADING_SCALE: list[GradingLevel] = [
    GradingLevel(id="not_attempted", description="Blank, off-topic, or shows no understanding of the question.", score=0),
    GradingLevel(
        id="some_effort", description="On-topic but misses most key points or has major misconceptions.", score=1
    ),
    GradingLevel(id="almost_there", description="Covers most key points with minor gaps or imprecision.", score=2),
    GradingLevel(id="solid", description="Covers the key points correctly and shows clear understanding.", score=3),
]


def default_grading_scale() -> list[GradingLevel]:
    return [level.model_copy() for level in DEFAULT_GRADING_SCALE]


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

    level: str = Field(description="Identifier of the assigned grading level (one of the rubric's grading_scale ids).")
    feedback: str = Field(description=_FEEDBACK_DESCRIPTION)


class GradeTrace(BaseModel):
    """Diagnostic record of one `grade_answer()` call -- everything beyond
    the `Grade` itself worth keeping around to later explain "why did the
    LLM grade this the way it did": the exact prompt sent, the
    provider/model used, whatever reasoning/"thinking" content that
    provider's response happened to expose, any tool calls made (e.g. the
    Python sandbox, see `sandbox.py`), token usage, and timing.

    DB-agnostic like `Grade`/`Question` (see their docstrings) -- produced by
    `grade_answer` via its optional `on_trace` callback; `worker/tasks.py` persists it as a `GradingTrace` row
    (`db/models.py`), in that table's `trace` JSONB column. `POST /rubrics/{id}/test-answer`'s synchronous "try it"
    endpoint doesn't pass `on_trace`, so nothing is captured there either --
    consistent with that endpoint's existing "nothing is persisted" promise.

    `thinking` is best-effort and frequently `None`: this project's only
    providers (`grading/llm.py` -- OpenAI, OpenRouter, Ollama) mostly don't
    expose raw reasoning tokens over the APIs used here, only occasionally a
    summary. See `grading.py`'s `_extract_thinking` for exactly what shapes
    are checked. Absence means "this provider/model/call didn't include
    any", not a bug.
    """

    provider: str
    model: str
    system_prompt: str
    student_answer: str
    grade: Grade
    # True for the blank-answer short-circuit (grade_answer never calls the
    # LLM at all in that case) -- system_prompt is still the prompt that
    # *would* have been sent, kept for completeness, but thinking/tool_calls/
    # token usage are necessarily empty/None and elapsed_seconds is ~0.
    blank_short_circuit: bool = False
    thinking: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    elapsed_seconds: float = 0.0
    # Images attached to the grading call (`Question.include_images`) --
    # just the count, the image data itself is never stored in a trace.
    image_count: int = 0
    # Compact summary (provider/model, tokens incl. provider-specific
    # sub-counts, cost if reported, features used, ...) -- see
    # grading.py's `build_grading_info`. Also copied onto
    # `AnsweredQuestion.grading_info` by the worker for the UI.
    info: dict[str, Any] = Field(default_factory=dict)


class GeneralFeedback(BaseModel):
    """A submission's general feedback (`Submission.general_feedback`):
    one short text digesting every question's grade and feedback, written by
    `general_feedback.py`'s `write_general_feedback` only when the rubric has
    `general_feedback_instructions`. `info` is the same compact audit summary as
    `AnsweredQuestion.grading_info`."""

    feedback: str
    info: dict[str, Any] = Field(default_factory=dict)


def grade_schema_for_scale(grading_scale: list[GradingLevel]) -> type[BaseModel]:
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
    levels = tuple(level.id for level in ordered_levels(grading_scale))
    return create_model(
        "GradeOutput",
        level=(Literal[levels], Field(description=f"Exactly one of: {', '.join(levels)}.")),
        feedback=(str, Field(description=_FEEDBACK_DESCRIPTION)),
    )


class SubmissionFormat(str, Enum):
    """Which file format a rubric's submissions are graded from.

    Set once on the `Rubric` (see `db/models.py`), not per
    `Submission`: a rubric's `Question.field` values only make sense under
    one format (a PDF AcroForm field name vs. a notebook cell tag), so a
    rubric authored for one format never accepts the other. Determines two
    things: which extractor in `grading/extraction/` turns the raw uploaded
    file into `{answer_key: text}`, and how `Question.field` is interpreted
    while doing that.
    """

    # A PDF form: answers are its named AcroForm text fields.
    PDF_FORM = "pdf_form"
    NOTEBOOK = "notebook"
    # Any PDF with no AcroForm fields, or a .docx converted to PDF on upload
    # (`UPLOAD_CONVERSIONS`). Converted to Markdown once, then split into
    # per-question answers ("segmentation", see
    # extraction/generic_document.py) by a professor, optionally helped by an
    # LLM suggestion (grading/segmentation.py). Graded only after that.
    GENERIC_DOCUMENT = "generic_document"


class ExtractedImage(BaseModel):
    """One image found in a submission (e.g. a notebook figure output),
    sent to the LLM as multimodal input when `Question.include_images` is
    set. `data` is base64, as stored in the source file."""

    mime_type: str
    data: str


class ExtractionResult(BaseModel):
    """What an extractor (`grading/extraction/`) returns for one submission file.

    `answers` is `{answer_key: text}`. `untagged_cells` is `{answer_key:
    count}` for every answer the extractor had to *infer* rather than read
    off an explicit marker -- today only the notebook extractor does this
    (cells a student added/recreated after a question, see
    `extraction/notebook.py`'s "Untagged cells" section); the PDF extractor
    always leaves it empty. Kept separate from `answers` so the worker can
    surface it per question (`AnsweredQuestion.untagged_cells`) for a TA to
    double-check, without it ever reaching the grading prompt.

    `answers_with_outputs` is the same as `answers` but with each cell's
    text outputs appended (used when `Question.include_outputs` is set), and
    `images` holds the images found per answer key (`Question.include_images`).
    Both stay empty for formats that have neither -- see
    `extraction/__init__.py`'s `OUTPUT_FORMATS`/`IMAGE_FORMATS`.
    """

    answers: dict[str, str] = Field(default_factory=dict)
    untagged_cells: dict[str, int] = Field(default_factory=dict)
    answers_with_outputs: dict[str, str] = Field(default_factory=dict)
    images: dict[str, list[ExtractedImage]] = Field(default_factory=dict)


class DocumentImage(BaseModel):
    """One image extracted while converting a generic document to Markdown,
    stored in object storage. `line` is the 1-based Markdown line holding its
    `![](name)` reference -- which segment that line falls into decides
    which answer the image belongs to."""

    name: str
    object_key: str
    line: int
    page: int
    mime_type: str


class ConvertedDocument(BaseModel):
    """Result of converting a generic document submission to Markdown (see
    extraction/generic_document.py). The Markdown itself and the images live in
    object storage, only this metadata is stored on the `Submission`.

    The stored Markdown is the coordinate system every `AnswerSegment` is
    expressed in -- it's generated once and never silently regenerated,
    since a different converter version could shift every line number."""

    markdown_object_key: str
    line_count: int
    # 1-based first Markdown line of each PDF page (index 0 = page 1).
    page_start_lines: list[int] = Field(default_factory=list)
    images: list[DocumentImage] = Field(default_factory=list)


class AnswerSegment(BaseModel):
    """Where one question's answer lives in a converted generic document, as
    1-based inclusive Markdown line numbers.

    `start_line=None` means "the student did not answer this question"
    (graded as a blank answer). `end_line=None` means "until the line before
    the next segment starts, or the end of the document" -- an explicit
    `end_line` lets a professor leave out dead lines between answers.
    A rubric question with no `AnswerSegment` at all is "not decided yet",
    which blocks grading."""

    question_id: str
    start_line: int | None = None
    end_line: int | None = None


class Question(BaseModel):
    """One gradable question: where to read the student's answer from the
    submitted file, plus grading instructions.

    DB-agnostic on purpose -- reused both as the grading-time input (see
    grading.py) and stored verbatim in the `Rubric`'s `questions` JSONB column.
    """

    id: str
    # Identifies where in the submitted file this question's answer lives --
    # meaning depends on the owning Rubric's `format` (see SubmissionFormat):
    # a PDF AcroForm text-field name for `pdf_form` (e.g. "answer1"), or the
    # value content of an `aitana/id` cell's metadata for `notebook`. For
    # `generic_document` it's an optional hint for LLM-assisted segmentation: the
    # label the question has in the student's document (e.g. "Exercise 3").
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
    # Optional worked/reference answer -- only set when the question has a
    # more or less canonical answer (e.g. "what command fixes this") rather
    # than an open-ended one where any number of phrasings are equally
    # valid. When set, it's included in the prompt as-is (see
    # grading/grading.py's build_system_prompt); most questions won't have
    # one, and `rubric` + `expected_points` remain the primary grading
    # criteria either way -- this is extra context, not a replacement.
    model_answer: str | None = None
    # When set, grade_answer (grading.py) gives the LLM a sandboxed Python
    # code-execution tool (grading/sandbox.py) instead of grading in one
    # structured-output call -- for questions where the corrector genuinely
    # benefits from running code (checking a claimed command's output,
    # verifying a computation) rather than judging prose alone. Off by
    # default: it's slower (an agent loop, not one call) and needs Deno
    # installed wherever grading runs (see grading/sandbox.py), so it's an
    # opt-in per question, not a rubric-wide switch -- most questions never
    # need it even within a rubric that has one that does.
    needs_python_sandbox: bool = False
    # Append the text outputs the student's code produced (notebook cell
    # outputs) to the answer sent to the LLM. Only for formats in
    # `extraction.OUTPUT_FORMATS` (validated on the Rubric).
    include_outputs: bool = False
    # Attach the images found in the answer (e.g. matplotlib figures in a
    # notebook's outputs) to the grading call as multimodal input -- needs a
    # vision-capable model. Only for formats in `extraction.IMAGE_FORMATS`.
    include_images: bool = False

    @property
    def answer_key(self) -> str:
        return self.field or self.id
