from aitana.grading.models import DEFAULT_GRADING_SCALE, GradingLevel
from aitana.grading.scoring import weighted_score

from .factories import question

SCALE = [
    GradingLevel(id="wrong", description="", score=-1),
    GradingLevel(id="blank", description="", score=0),
    GradingLevel(id="ok", description="", score=2),
    GradingLevel(id="great", description="", score=5),
]


def test_unweighted_default_scale():
    score = weighted_score([question("a"), question("b")], {"a": "solid", "b": "some_effort"}, DEFAULT_GRADING_SCALE)
    assert (score.raw, score.maximum, score.complete) == (4, 6, True)
    assert round(score.percent, 4) == round(4 / 6 * 100, 4)


def test_weights_multiply_level_scores():
    questions = [question("a", weight=3), question("b", weight=0.5)]
    score = weighted_score(questions, {"a": "ok", "b": "great"}, SCALE)
    assert score.raw == 3 * 2 + 0.5 * 5
    assert score.maximum == 3.5 * 5
    assert score.complete


def test_ungraded_counts_in_the_maximum_and_marks_incomplete():
    score = weighted_score([question("a"), question("b")], {"a": "great", "b": None}, SCALE)
    assert (score.raw, score.maximum, score.complete) == (5, 10, False)
    assert score.percent == 50


def test_unknown_level_is_ungraded():
    score = weighted_score([question("a")], {"a": "removed"}, SCALE)
    assert (score.raw, score.complete) == (0, False)


def test_zero_weight_question_is_ignored_even_when_ungraded():
    score = weighted_score([question("a"), question("b", weight=0)], {"a": "ok"}, SCALE)
    assert (score.raw, score.maximum, score.complete) == (2, 5, True)


def test_negative_levels_can_go_below_zero():
    score = weighted_score([question("a")], {"a": "wrong"}, SCALE)
    assert score.percent == -20
