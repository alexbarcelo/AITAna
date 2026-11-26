"""A submission's weighted score -- mirrored by the frontend's `submissionScore`
(`frontend/src/lib/gradeStats.ts`); keep both in sync.

`raw` = sum over the rubric's questions of `weight x level score`; `maximum`
= the same with every question at the scale's top level; `percent` = `raw /
maximum x 100`. The denominator always covers *every* question of the
rubric (not just the graded ones), so a submission whose grading isn't
finished never looks better than it is: `complete` says whether every
question that counts (weight > 0) has a grade the scale knows. Exports only
report the score when it is complete.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from aitana.grading.models import GradingLevel, Question


class Score(BaseModel):
    raw: float
    maximum: float
    complete: bool

    @property
    def percent(self) -> float | None:
        return self.raw / self.maximum * 100 if self.maximum > 0 else None


def weighted_score(
    questions: list[Question], levels: Mapping[str, str | None], grading_scale: list[GradingLevel]
) -> Score:
    """`levels` maps question id -> effective grade level (missing/`None` =
    ungraded). Ungraded answers (and levels no longer in the scale) add 0 to
    `raw` and make the score incomplete."""
    scores = {level.id: level.score for level in grading_scale}
    top = max(scores.values())
    raw = maximum = 0.0
    complete = True
    for question in questions:
        if question.weight == 0:
            continue
        maximum += question.weight * top
        level = levels.get(question.id)
        if level is None or level not in scores:
            complete = False
            continue
        raw += question.weight * scores[level]
    return Score(raw=raw, maximum=maximum, complete=complete)
