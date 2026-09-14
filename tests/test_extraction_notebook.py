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


def _cell(cell_type: str, source, tags: list[str] | None = None, field: str | None = None) -> dict:
    metadata: dict = {}
    if tags is not None:
        metadata["tags"] = tags
    if field is not None:
        metadata["aitana"] = {"id": field}
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

    answers = extract_answers(notebook_path)

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

    answers = extract_answers(notebook_path)

    assert answers == {"answer1": "df = load()\n\nBecause the data is skewed."}


def test_extract_answers_ignores_non_aitana_tags(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "1 + 1", tags=["parameters", "other:thing"])])

    assert extract_answers(notebook_path) == {}


def test_extract_answers_ignores_answer_tag_without_field_metadata(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "1 + 1", tags=["aitana:answer"])])

    assert extract_answers(notebook_path) == {}


def test_extract_answers_blank_cell_is_still_reported(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    _write_notebook(notebook_path, [_cell("code", "", tags=["aitana:answer"], field="answer1")])

    assert extract_answers(notebook_path) == {"answer1": ""}


def test_extract_answers_rejects_pre_v4_nbformat(tmp_path):
    notebook_path = tmp_path / "old.ipynb"
    _write_notebook(notebook_path, [], nbformat=3)

    with pytest.raises(ValueError, match="nbformat"):
        extract_answers(notebook_path)
