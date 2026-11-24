"""LLM-assisted segmentation of a generic document submission.

Given the Markdown a PDF was converted to (`extraction/generic_document.py`) and
a rubric's questions, ask an LLM on which line each answer starts. The
result is only a suggestion: the submission still waits for a professor to
review it in the segmentation editor before anything is graded.

The document is sent with every line numbered (`number_lines`) -- the same
1-based line numbers segments are stored in, so the answer needs no fuzzy
text matching to map back. The LLM only picks start lines; end lines (to
skip dead lines between answers) are a professor-only refinement.
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
    )
    return create_model(
        "Segmentation",
        answers=(list[item], Field(description="One entry per question.")),  # type: ignore[valid-type]
    )


def suggest_segments(chat_model: BaseChatModel, markdown: str, questions: list[Question]) -> list[AnswerSegment]:
    """Ask the LLM where each answer starts. Sanitizes the result instead of
    trusting it: out-of-range lines and a line claimed twice become `None`
    for the later question, and every question gets exactly one segment --
    so the suggestion always passes `validate_segments`."""
    line_count = len(markdown.split("\n"))
    agent = create_agent(model=chat_model, tools=[], response_format=segmentation_schema(questions))
    start = time.perf_counter()
    result = agent.invoke(
        {"messages": [SystemMessage(content=build_system_prompt(questions)), HumanMessage(content=number_lines(markdown))]}
    )
    raw = result["structured_response"]
    logger.info("Segmentation suggested in %.2fs", time.perf_counter() - start)

    by_id: dict[str, int | None] = {}
    for answer in raw.answers:
        by_id.setdefault(answer.question_id, answer.start_line)

    used: set[int] = set()
    segments: list[AnswerSegment] = []
    for q in questions:
        line = by_id.get(q.id)
        if line is not None and (not 1 <= line <= line_count or line in used):
            logger.info("Segmentation: discarding line %s suggested for %s", line, q.id)
            line = None
        if line is not None:
            used.add(line)
        segments.append(AnswerSegment(question_id=q.id, start_line=line))
    return segments
