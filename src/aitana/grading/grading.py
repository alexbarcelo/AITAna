"""Builds the grading prompt and invokes the LLM for a single question.

Prompt split:
  - SYSTEM message: grading instructions (rubric, expected points, grading
    scale) -- comes from the YAML config / rubric document, same for every
    student.
  - HUMAN message: the student's own answer text, verbatim.
"""

from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .models import Grade, Question, grade_schema_for_scale
from .sandbox import get_python_sandbox_tool

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

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
{python_tool_section}
# Grading
Assign exactly one level:
{grading_levels}

Then give short (2-3 sentence), specific feedback addressed directly to the
student: what they got right and, more importantly, what they are missing.
"""

# Only interpolated when Question.needs_python_sandbox is set (see
# build_system_prompt) -- the tool's own description (grading/sandbox.py)
# already tells the model how to call it; this just tells it *when* it's
# worth bothering, since most rubric text alone wouldn't suggest running code.
_PYTHON_TOOL_SECTION = """
# Tool available
You have a Python sandbox tool. Use it if running code would help you judge
this answer -- e.g. to check a claimed command's output or verify a
computation -- but it's optional: skip it if the rubric can be judged from
the answer text alone.
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
    python_tool_section = _PYTHON_TOOL_SECTION if question.needs_python_sandbox else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        question=question.question.strip(),
        context_section=context_section,
        rubric=question.rubric.strip(),
        model_answer_section=model_answer_section,
        expected_points=points,
        python_tool_section=python_tool_section,
        grading_levels=build_grading_section(grading_scale),
    )


def _grade_with_tools(chat_model: BaseChatModel, tools: list[BaseTool], system_prompt: str, student_answer: str, schema: type) -> object:
    """Call an agent with the available tools for this answer.
    Note that this is typically overkill for no-tool answers, but
    you know: DRY.
    """
    agent = create_agent(
        model=chat_model,
        tools=tools,
        response_format=schema,
    )
    result = agent.invoke({"messages": [SystemMessage(content=system_prompt), HumanMessage(content=student_answer)]})
    return result["structured_response"]


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

    schema = grade_schema_for_scale(grading_scale)
    system_prompt = build_system_prompt(question, grading_scale)
    logger.debug("%s: system prompt:\n%s", question.id, system_prompt)

    logger.info("%s: calling LLM%s...", question.id, " (with python sandbox)" if question.needs_python_sandbox else "")
    start = time.perf_counter()

    tools = []
    if question.needs_python_sandbox:
        tools.append(get_python_sandbox_tool())

    raw = _grade_with_tools(chat_model, tools, system_prompt, student_answer, schema)

    # Normalize back to the stable `Grade` shape -- `raw` is an instance of
    # the one-off schema grade_schema_for_scale() just built, not `Grade`
    # itself (see that function's docstring for why the two are separate).
    grade = Grade(level=raw.level, feedback=raw.feedback)
    elapsed = time.perf_counter() - start
    logger.info("%s: graded as %s in %.2fs", question.id, grade.level, elapsed)
    logger.debug("%s feedback: %s", question.id, grade.feedback)
    return grade
