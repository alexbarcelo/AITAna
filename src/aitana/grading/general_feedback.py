"""General feedback: one short, submission-wide feedback text.

Optional, per rubric: only written when the rubric has
`general_feedback_instructions` (`Rubric.general_feedback_instructions`), which tell the LLM
how to digest the per-question results into a single short feedback. Runs
once, after every question has been graded (`worker/tasks.py`).

Prompt split, same as `grading.py`:
  - SYSTEM message: the rubric-wide context, the grading scale (ids +
    descriptions -- never scores, same as per-question grading) and the
    general feedback instructions.
  - HUMAN message: for each question, in rubric order, its short name
    (`Question.title`), its grade level and its feedback -- the effective
    grade, so a TA's manual override is what the general feedback is based on. The
    student's answers themselves are not sent: the general feedback digests the
    grading, it doesn't re-grade.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .grading import _sum_token_details, build_grading_section
from .models import GeneralFeedback, Grade, GradingLevel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are a teaching assistant writing the general feedback for a
master's-level student's submission. Every question has already been
graded; the user message lists, for each question, its name, the grade
level it got and the feedback the student received for it. Digest them
into a single short feedback addressed directly to the student, following
the instructions below. Do not re-grade the questions and do not repeat
every question's feedback.
{rubric_context_section}
# Grading scale
The levels each question could get, worst to best:
{grading_levels}

# Instructions for the general feedback
{instructions}
"""


@dataclass
class GeneralFeedbackItem:
    """One graded question, as the general feedback sees it. `grade` is `None` for a
    question that has no grade (e.g. its grading failed)."""

    title: str
    grade: Grade | None


class _GeneralFeedbackOutput(BaseModel):
    feedback: str = Field(description="The general feedback, addressed directly to the student.")


def has_general_feedback(instructions: str | None) -> bool:
    return bool(instructions and instructions.strip())


def build_system_prompt(
    instructions: str, grading_scale: list[GradingLevel], *, rubric_context: str | None = None
) -> str:
    rubric_context_section = (
        f"\n# Assignment context\n{rubric_context.strip()}\n" if rubric_context and rubric_context.strip() else ""
    )
    return SYSTEM_PROMPT_TEMPLATE.format(
        rubric_context_section=rubric_context_section,
        grading_levels=build_grading_section(grading_scale),
        instructions=instructions.strip(),
    )


def build_human_message(items: list[GeneralFeedbackItem]) -> HumanMessage:
    blocks = []
    for item in items:
        if item.grade is None:
            blocks.append(f"## {item.title}\nGrade: (not graded)")
        else:
            blocks.append(f"## {item.title}\nGrade: {item.grade.level}\nFeedback: {item.grade.feedback.strip()}")
    return HumanMessage(content="\n\n".join(blocks))


def _general_feedback_info(
    *, provider: str, model: str, reasoning_effort: str | None, messages: list[BaseMessage], elapsed: float
) -> dict[str, Any]:
    """Compact audit info, the same sparse shape as `build_grading_info`'s
    (minus the per-question keys), so the frontend's tooltip renders it."""
    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
    info: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "graded_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "llm_calls": len(ai_messages),
        "reasoning_effort": reasoning_effort,
    }
    if tokens := _sum_token_details(messages):
        info["tokens"] = tokens
    costs = [
        m.response_metadata["cost"] for m in ai_messages if isinstance(m.response_metadata.get("cost"), int | float)
    ]
    if costs:
        info["cost"] = sum(costs)
    return info


async def write_general_feedback(
    chat_model: BaseChatModel,
    instructions: str,
    grading_scale: list[GradingLevel],
    items: list[GeneralFeedbackItem],
    *,
    rubric_context: str | None = None,
    provider: str = "unknown",
    model: str = "unknown",
    reasoning_effort: str | None = None,
) -> GeneralFeedback:
    """One LLM call digesting `items` into a single feedback, following
    `instructions` (the rubric's `general_feedback_instructions`)."""
    system_prompt = build_system_prompt(instructions, grading_scale, rubric_context=rubric_context)
    logger.debug("General feedback system prompt:\n%s", system_prompt)
    start = time.perf_counter()
    agent = create_agent(model=chat_model, tools=[], response_format=_GeneralFeedbackOutput)
    result = await agent.ainvoke({"messages": [SystemMessage(content=system_prompt), build_human_message(items)]})
    elapsed = time.perf_counter() - start
    raw = result["structured_response"]
    logger.info("General feedback written in %.2fs", elapsed)
    return GeneralFeedback(
        feedback=raw.feedback.strip(),
        info=_general_feedback_info(
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            messages=result.get("messages", []),
            elapsed=elapsed,
        ),
    )
