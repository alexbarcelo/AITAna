"""Covers the format -> extractor dispatch itself (grading/extraction/__init__.py),
as opposed to tests/test_extraction_pdf.py and tests/test_extraction_notebook.py,
which cover each individual extractor's parsing logic."""

import json

from aitana.grading.extraction import FORMAT_FILE_INFO, extract_answers
from aitana.grading.models import SubmissionFormat


def test_extract_answers_dispatches_to_notebook_extractor(tmp_path):
    notebook_path = tmp_path / "submission.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "source": "42",
                        "metadata": {"tags": ["aitana:answer"], "aitana": {"id": "answer1"}},
                        "outputs": [],
                    }
                ],
            }
        )
    )

    assert extract_answers(SubmissionFormat.NOTEBOOK, notebook_path) == {"answer1": "42"}


def test_format_file_info_covers_every_submission_format():
    # Every SubmissionFormat needs a registered extension/content-type --
    # storage.object_key() and the download endpoint both key off this dict,
    # so a missing entry would only surface as a runtime KeyError on upload.
    assert set(FORMAT_FILE_INFO) == set(SubmissionFormat)
