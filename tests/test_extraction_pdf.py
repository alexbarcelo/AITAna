from pathlib import Path

from aitana.grading.extraction.pdf import extract_answers

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_extract_answers_from_sample_pdf():
    result = extract_answers(REPO_ROOT / "containers-example.pdf")
    answers = result.answers
    assert result.untagged_cells == {}
    assert answers
    assert any(v for v in answers.values()), "expected at least one non-blank answer field"
