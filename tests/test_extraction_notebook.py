import json

import pytest

from aitana.grading.extraction.notebook import extract_answers


def _write_notebook(path, cells: list[dict], nbformat: int = 4) -> None:
    notebook = {
        "nbformat": nbformat,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": cells,
    }
    path.write_text(json.dumps(notebook))


def _cell(
    cell_type: str, source, tags: list[str] | None = None, field: str | None = None, editable: bool | None = None
) -> dict:
    metadata: dict = {}
    if tags is not None:
        metadata["tags"] = tags
    if field is not None:
        metadata["aitana"] = {"id": field}
    if editable is not None:
        metadata["editable"] = editable
    return {
        "cell_type": cell_type,
        "source": source,
        "metadata": metadata,
        "outputs": [],
    }


def test_extract_answers_reads_aitana_tagged_cells(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [
            _cell("markdown", ["# Lab\n"]),
            _cell("code", ["import pandas as pd\n", "pd.read_csv('x')"], tags=["aitana:answer"], field="answer1"),
            _cell("code", ["print('untagged, not an answer')"]),
            _cell("markdown", ["Explanation for question 2"], tags=["aitana:answer"], field="answer2"),
        ],
    )

    answers = extract_answers(notebook_path).answers

    assert answers == {
        "answer1": "import pandas as pd\npd.read_csv('x')",
        "answer2": "Explanation for question 2",
    }


def test_extract_answers_concatenates_multiple_cells_sharing_a_field(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [
            _cell("code", "df = load()", tags=["aitana:answer"], field="answer1"),
            _cell("markdown", "Because the data is skewed.", tags=["aitana:answer"], field="answer1"),
        ],
    )

    answers = extract_answers(notebook_path).answers

    assert answers == {"answer1": "df = load()\n\nBecause the data is skewed."}


def test_extract_answers_ignores_non_aitana_tags(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "1 + 1", tags=["parameters", "other:thing"])])

    assert extract_answers(notebook_path).answers == {}


def test_extract_answers_ignores_answer_tag_without_field_metadata(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "1 + 1", tags=["aitana:answer"])])

    assert extract_answers(notebook_path).answers == {}


def test_extract_answers_blank_cell_is_still_reported(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "", tags=["aitana:answer"], field="answer1")])

    assert extract_answers(notebook_path).answers == {"answer1": ""}


def test_extract_answers_rejects_pre_v4_nbformat(tmp_path):
    notebook_path = tmp_path / "old.ipynb"
    _write_notebook(notebook_path, [], nbformat=3)

    with pytest.raises(ValueError, match="nbformat"):
        extract_answers(notebook_path)


@pytest.mark.parametrize("source", ["...", "  ...\n", ["\n", "...\n", "\n"], "   \n\n\t", ["\n", "  \n"]])
def test_extract_answers_placeholder_or_whitespace_cell_is_blank(tmp_path, source):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", source, tags=["aitana:answer"], field="answer1")])

    assert extract_answers(notebook_path).answers == {"answer1": ""}


def test_extract_answers_placeholder_part_leaves_no_stray_separator(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [
            _cell("code", "...", tags=["aitana:answer"], field="answer1"),
            _cell("code", "df = load()", tags=["aitana:answer"], field="answer1"),
            _cell("markdown", "  \n", tags=["aitana:answer"], field="answer1"),
            _cell("code", "...", tags=["aitana:answer"], field="answer2"),
            _cell("code", "\n...\n", tags=["aitana:answer"], field="answer2"),
        ],
    )

    assert extract_answers(notebook_path).answers == {"answer1": "df = load()", "answer2": ""}


@pytest.mark.parametrize("source", ["x[..., 0]", "def f():\n    ...", "...\nprint(1)"])
def test_extract_answers_ellipsis_inside_real_code_is_kept(tmp_path, source):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", source, tags=["aitana:answer"], field="answer1")])

    assert extract_answers(notebook_path).answers == {"answer1": source}


def _question(field: str) -> dict:
    # Same shape as a real template's question cell: locked, tagged, with an id.
    return _cell("markdown", f"Question {field}", tags=["aitana:question"], field=field, editable=False)


def _answer(field: str, source) -> dict:
    return _cell("code", source, tags=["aitana:answer"], field=field, editable=True)


def test_untagged_cells_after_question_are_joined_and_counted(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [
            _cell("markdown", "# Intro", editable=False),
            _cell("code", "print('before any question')"),
            _question("q1"),
            _answer("q1", "a = 1"),
            _cell("code", "print(a)"),  # student-added, no metadata at all
            _cell("markdown", "It prints 1."),
            _question("q2"),
            _answer("q2", "b = 2"),
        ],
    )

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "a = 1\n\nprint(a)\n\nIt prints 1.", "q2": "b = 2"}
    assert result.untagged_cells == {"q1": 2}


def test_recreated_answer_cell_without_tags_is_still_read(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [_question("q1"), _cell("code", "x = 42"), _question("q2"), _answer("q2", "...")],
    )

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "x = 42", "q2": ""}
    assert result.untagged_cells == {"q1": 1}


def test_non_editable_cell_closes_the_question_region(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [
            _question("q1"),
            _answer("q1", "a = 1"),
            _cell("markdown", "## Part 2 (locked section header)", editable=False),
            _cell("code", "scratch()"),
        ],
    )

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "a = 1"}
    assert result.untagged_cells == {}


def test_blank_untagged_cells_are_not_counted(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [_question("q1"), _answer("q1", "a = 1"), _cell("code", ""), _cell("code", "..."), _cell("markdown", "  \n")],
    )

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "a = 1"}
    assert result.untagged_cells == {}


def test_explicit_answer_tag_wins_over_position(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_question("q1"), _answer("q1", "a"), _answer("q2", "b")])

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "a", "q2": "b"}
    assert result.untagged_cells == {}


def test_answer_tag_without_id_inside_question_region_counts_as_untagged(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_question("q1"), _cell("code", "a = 1", tags=["aitana:answer"])])

    result = extract_answers(notebook_path)

    assert result.answers == {"q1": "a = 1"}
    assert result.untagged_cells == {"q1": 1}


def test_question_without_id_does_not_collect_cells(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(
        notebook_path,
        [_cell("markdown", "Q?", tags=["aitana:question"], editable=False), _cell("code", "a = 1")],
    )

    result = extract_answers(notebook_path)

    assert result.answers == {}
    assert result.untagged_cells == {}
