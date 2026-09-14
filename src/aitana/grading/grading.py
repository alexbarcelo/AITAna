"""Builds the grading prompt and invokes the LLM for a single question.

Prompt split:
  - SYSTEM message: grading instructions (rubric, expected points, grading
    scale) -- comes from the YAML config / rubric document, same for every
    student.
  - HUMAN message: the student's own answer text, verbatim.
"""

import logging
import time

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .models import Grade, Question, grade_schema_for_scale

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are a teaching assistant grading a master's-level student's answer to a
hands-on lab question. Grade at a COARSE level only: the goal is fast,
useful feedback for the student and teacher, not a precise score.

# Question
{question}
{context_section}
# Grading instructions
{rubric}
{model_answer_section}
# Points a good answer should cover
{expected_points}

# Grading
Assign exactly one level:
{grading_levels}

Then give short (2-3 sentence), specific feedback addressed directly to the
student: what they got right and, more importantly, what they are missing.
"""


def build_grading_section(grading_scale: dict[str, str]) -> str:
    """Render a rubric's grading_scale as the prompt's level-id: description
    list -- the part of the prompt that used to be a hardcoded 4-line block
    in SYSTEM_PROMPT_TEMPLATE itself, before grading scales became
    per-rubric (see Rubric.grading_scale's docstring)."""
    return "\n".join(f"- {level}: {description}" for level, description in grading_scale.items())


def build_system_prompt(question: Question, grading_scale: dict[str, str]) -> str:
    points = "\n".join(f"- {p}" for p in question.expected_points) or "(none specified)"
    context_section = f"\n# Context\n{question.context.strip()}\n" if question.context else ""
    # Only rendered when the question has one (see Question.model_answer's
    # docstring) -- most questions don't, and omitting the heading entirely
    # avoids implying "no canonical answer" is itself meaningful feedback.
    model_answer_section = f"\n# Model answer\n{question.model_answer.strip()}\n" if question.model_answer else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        question=question.question.strip(),
        context_section=context_section,
        rubric=question.rubric.strip(),
        model_answer_section=model_answer_section,
        expected_points=points,
        grading_levels=build_grading_section(grading_scale),
    )


def grade_answer(chat_model: BaseChatModel, question: Question, student_answer: str, grading_scale: dict[str, str]) -> Grade:
    logger.debug("Grading %s: %d char(s) of answer text", question.id, len(student_answer))

    if not student_answer.strip():
        # Short-circuit blank answers instead of paying for an LLM call.
        # `grading_scale`'s first entry is, by convention, the "worst/no
        # answer" level -- see Rubric.grading_scale's docstring.
        # TODO: revisit if we ever want LLM-phrased feedback for blanks too.
        worst_level = next(iter(grading_scale))
        logger.info("%s: blank answer, skipping LLM call", question.id)
        return Grade(level=worst_level, feedback="No answer was provided for this question.")

    # TODO: with_structured_output relies on tool-calling support. Some
    # (especially small local Ollama) models don't support it reliably --
    # add a JSON-in-prompt fallback + try/except here if that turns out to
    # be a real problem in practice.
    structured_model = chat_model.with_structured_output(grade_schema_for_scale(grading_scale))
    messages = [
        SystemMessage(content=build_system_prompt(question, grading_scale)),
        HumanMessage(content=student_answer),
    ]
    logger.debug("%s: system prompt:\n%s", question.id, messages[0].content)

    logger.info("%s: calling LLM...", question.id)
    start = time.perf_counter()
    raw = structured_model.invoke(messages)
    # Normalize back to the stable `Grade` shape -- `raw` is an instance of
    # the one-off schema grade_schema_for_scale() just built, not `Grade`
    # itself (see that function's docstring for why the two are separate).
    grade = Grade(level=raw.level, feedback=raw.feedback)
    elapsed = time.perf_counter() - start
    logger.info("%s: graded as %s in %.2fs", question.id, grade.level, elapsed)
    logger.debug("%s feedback: %s", question.id, grade.feedback)
    return grade
