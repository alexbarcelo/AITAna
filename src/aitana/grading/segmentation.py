"""LLM-assisted segmentation of a generic document submission.

Given the Markdown a PDF was converted to (`extraction/generic_document.py`) and
a rubric's questions, ask an LLM on which line each answer starts. The
result is only a suggestion: the submission still waits for a professor to
review it in the segmentation editor before anything is graded.

The document is sent with every line numbered (`number_lines`) -- the same
1-based line numbers segments are stored in, so the answer needs no fuzzy
text matching to map back. The LLM picks start lines and, only when it
sees dead lines between answers worth leaving out, an end line; otherwise
the end is implicit (`resolve_segments`). The professor can refine either.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from .extraction.generic_document import number_lines
from .models import AnswerSegment, Question

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are helping a teacher split a student's submission into the answers to
each question of an assignment. The submission was converted from PDF to
Markdown; the user message contains it with every line prefixed by its line
number (`  42| text`). Images were replaced by `[image]`.

For each question below, give the line number where the student's answer
to that question starts. If the student restated the question or wrote a
heading for it (e.g. "Exercise 3", "Answer 3"), the answer starts at that
line. If the student did not answer a question at all, use null. Answers
usually appear in order, but not always. Never give the same line to two
questions.

An answer normally runs until the line before the next answer starts (or
the end of the document), so leave the end line null. Only give an end
line (the last line of the answer, inclusive) when there is content
between this answer and the next one that belongs to no answer -- e.g.
unrelated notes, a cover/annex section, or the rest of the document after
the last answer. When in doubt, leave it null.

# Questions
{questions}
"""


def _describe_question(q: Question) -> str:
    parts = [f"## id: {q.id}", f"Title: {q.title}"]
    if q.field:
        parts.append(f"Label in the document: {q.field}")
    parts.append(f"Statement: {q.question.strip()}")
    return "\n".join(parts)


def build_system_prompt(questions: list[Question]) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(questions="\n\n".join(_describe_question(q) for q in questions))


def segmentation_schema(questions: list[Question]) -> type[BaseModel]:
    """One-off structured-output schema constraining `question_id` to this
    rubric's ids -- same per-call trick as `grade_schema_for_scale`."""
    ids = tuple(q.id for q in questions)
    item = create_model(
        "AnswerStart",
        question_id=(Literal[ids], Field(description="One of the question ids.")),
        start_line=(int | None, Field(description="Line where the answer starts, or null if not answered.")),
        end_line=(
            int | None,
            Field(
                default=None,
                description="Last line of the answer (inclusive). Null (the usual case) = up to the next answer.",
            ),
        ),
    )
    return create_model(
        "Segmentation",
        answers=(list[item], Field(description="One entry per question.")),  # type: ignore[valid-type]
    )


def suggest_segments(chat_model: BaseChatModel, markdown: str, questions: list[Question]) -> list[AnswerSegment]:
    """Ask the LLM where each answer starts (and, optionally, ends).
    Sanitizes the result instead of trusting it: out-of-range lines and a
    line claimed twice become `None` for the later question, an end line
    before its start, past the document or reaching into the next answer is
    dropped (implicit end instead), and every question gets exactly one
    segment -- so the suggestion always passes `validate_segments`."""
    line_count = len(markdown.split("\n"))
    agent = create_agent(model=chat_model, tools=[], response_format=segmentation_schema(questions))
    start = time.perf_counter()
    result = agent.invoke(
        {"messages": [SystemMessage(content=build_system_prompt(questions)), HumanMessage(content=number_lines(markdown))]}
    )
    raw = result["structured_response"]
    logger.info("Segmentation suggested in %.2fs", time.perf_counter() - start)

    by_id: dict[str, tuple[int | None, int | None]] = {}
    for answer in raw.answers:
        by_id.setdefault(answer.question_id, (answer.start_line, getattr(answer, "end_line", None)))

    used: set[int] = set()
    starts: dict[str, int | None] = {}
    for q in questions:
        line = by_id.get(q.id, (None, None))[0]
        if line is not None and (not 1 <= line <= line_count or line in used):
            logger.info("Segmentation: discarding line %s suggested for %s", line, q.id)
            line = None
        if line is not None:
            used.add(line)
        starts[q.id] = line

    placed = sorted(line for line in starts.values() if line is not None)
    segments: list[AnswerSegment] = []
    for q in questions:
        start_line = starts[q.id]
        end_line = by_id.get(q.id, (None, None))[1] if start_line is not None else None
        if end_line is not None:
            assert start_line is not None
            next_start = next((s for s in placed if s > start_line), line_count + 1)
            if not start_line <= end_line < next_start:
                logger.info("Segmentation: discarding end line %s suggested for %s", end_line, q.id)
                end_line = None
        segments.append(AnswerSegment(question_id=q.id, start_line=start_line, end_line=end_line))
    return segments
