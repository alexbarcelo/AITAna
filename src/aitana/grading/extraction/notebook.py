"""Extracts student answers from a Jupyter notebook (.ipynb) submission.

One of the pluggable extractors dispatched by `extraction/__init__.py`'s
`extract_answers(format, path)` for `SubmissionFormat.NOTEBOOK` rubrics.

## Why this parses the JSON directly instead of using `nbformat`

`.ipynb` *is* JSON -- a top-level `cells` list, each cell a dict with
`cell_type`, `source`, and `metadata` -- specified by the nbformat schema
(https://nbformat.readthedocs.io/), stable at version 4 since ~2015 (every
current Jupyter/JupyterLab/Colab/VS Code export produces v4). This module
only ever *reads* three of those fields (`cells`, `cell.source`,
`cell.metadata.tags`) from notebooks written against this project's own
tagging convention below -- it never validates, upgrades, or rewrites a
notebook.

That's exactly the part of the job the official `nbformat` package doesn't
add much for here: its value is schema *validation* (`nbformat.validate()`),
converting older versions forward (`nbformat.v3` -> `v4`, which also
restructures cells -- `cell.input` instead of `cell.source`, an extra
`worksheets` nesting level), and a richer `NotebookNode` object model for
*writing* notebooks back out. None of that applies to a read-only extractor
consuming five fields -- pulling in the dependency would trade a couple of
`dict.get()` calls for a new package plus an API surface (version
converters, `NotebookNode`) this code would never exercise. The trade-off we
accept by not using it: a genuinely old nbformat-v3 notebook won't be
silently misread, but it also won't be auto-upgraded -- `extract_answers`
below raises a clear error on anything older than v4 instead. Given the
target audience (students exporting from current Jupyter/Colab), that's a
non-issue in practice; if it ever becomes one, that's the point to revisit
`nbformat` for its converters specifically, not for routine parsing.

## Tagging convention

A cell answers question `<field>` (`Question.answer_key`, i.e. `field` or
`id`) when it carries the cell tag `aitana:answer` (`cell.metadata.tags:
list[str]` -- Jupyter's built-in per-cell "tags" mechanism, editable via
Jupyter/JupyterLab's own "Cell Tags" UI, no custom extension needed) *and*
`cell.metadata.aitana.id` is set to `<field>` (set via Jupyter/JupyterLab's
built-in "Advanced Tools" metadata editor). A lab template author tags the
cell(s) where students are meant to write question 1's answer with
`aitana:answer` and sets `aitana.id` to `answer1`, etc. -- the notebook
equivalent of naming a PDF AcroForm field `answer1`.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TAG_ANSWER = "aitana:answer"
_MIN_NBFORMAT_VERSION = 4


def _cell_source(cell: dict) -> str:
    # nbformat v4 stores `source` as either one string or a list of line
    # strings (so notebook diffs are line-granular) -- normalize both.
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else (source or "")


def extract_answers(notebook_path: Path) -> dict[str, str]:
    """Return {field: text_value} for every `aitana:answer`-tagged cell."""
    logger.debug("Opening notebook %s", notebook_path)
    with notebook_path.open(encoding="utf-8") as f:
        notebook = json.load(f)

    nbformat_version = notebook.get("nbformat")
    if nbformat_version is not None and nbformat_version < _MIN_NBFORMAT_VERSION:
        raise ValueError(
            f"{notebook_path}: unsupported notebook format version {nbformat_version} "
            f"(need nbformat >= {_MIN_NBFORMAT_VERSION}; re-save from a current Jupyter to upgrade it)"
        )

    # An id can appear on more than one cell (e.g. a code cell plus a
    # markdown cell both answering question 1) -- concatenate them in
    # notebook order rather than keeping only the first or last match.
    parts_by_field: dict[str, list[str]] = {}
    for cell in notebook.get("cells", []):
        if _TAG_ANSWER not in cell.get("metadata", {}).get("tags", []):
            continue

        # At this point, we have filtered to only the cells tagged with `aitana:answer`. 
        # Now we need to find the `<field>` value on the aitana/id metadata
        aitana_metadata = cell.get("metadata", {}).get("aitana", {})
        if not isinstance(aitana_metadata, dict) or "id" not in aitana_metadata:
            logger.warning("Cell %s has invalid aitana metadata: %s", cell.get("id"), aitana_metadata)
            continue
        field = aitana_metadata["id"]
        parts_by_field.setdefault(field, []).append(_cell_source(cell).strip())

    answers = {field: "\n\n".join(parts).strip() for field, parts in parts_by_field.items()}
    non_blank = sum(1 for v in answers.values() if v)
    logger.info("Extracted %d tagged cell group(s) from %s (%d non-blank)", len(answers), notebook_path, non_blank)
    logger.debug("Fields: %s", list(answers.keys()))
    return answers
