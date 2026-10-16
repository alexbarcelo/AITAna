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

A tagged cell whose whole source is just `...` (the Python Ellipsis
placeholder exercise templates leave in an answer cell), or only whitespace,
counts as blank: an untouched template answer extracts as `""` (the field key
is still reported), so `grade_answer`'s blank-answer short-circuit applies
instead of an LLM call grading the placeholder itself. `...` inside real code
(`x[..., 0]`, or alongside other lines) is left alone.

## Untagged cells (students don't always respect the template)

Students delete and recreate an answer cell (losing its tags), or add extra
cells below it to try things out. To not lose those answers, a template can
also tag the cell *stating* each question with `aitana:question` (plus the
same `aitana.id`). From such a cell on, every following **editable** cell
(`metadata.editable` not `false`) belongs to that question, until the next
non-editable cell or the next `aitana:question` cell -- templates lock their
question/instruction cells (`editable: false`), so that's where a student's
answer area naturally ends. Within that region:

- an `aitana:answer` cell is read as usual, under its *own* `aitana.id`
  (an explicit tag always wins over position);
- any other non-blank cell (code, markdown or raw -- including an
  `aitana:answer` cell that lost its `aitana.id`) is joined into that
  question's answer, in notebook order, and counted in
  `ExtractionResult.untagged_cells[<id>]` so the UI can flag it for a TA.
  Blank/placeholder cells are skipped silently: they add nothing to the
  answer, so there's nothing to double-check either.

Cells before the first `aitana:question` cell, or after a non-editable cell
closes a region, are ignored as before. A notebook with no `aitana:question`
cells at all behaves exactly like the tag-only convention above.
"""

import json
import logging
from pathlib import Path

from ..models import ExtractionResult

logger = logging.getLogger(__name__)

_TAG_ANSWER = "aitana:answer"
_TAG_QUESTION = "aitana:question"
_MIN_NBFORMAT_VERSION = 4
_PLACEHOLDER_SOURCE = "..."


def _cell_source(cell: dict) -> str:
    # nbformat v4 stores `source` as either one string or a list of line
    # strings (so notebook diffs are line-granular) -- normalize both.
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else (source or "")


def _aitana_id(metadata: dict) -> str | None:
    aitana_metadata = metadata.get("aitana", {})
    if isinstance(aitana_metadata, dict) and isinstance(aitana_metadata.get("id"), str):
        return aitana_metadata["id"]
    return None


def _answer_text(cell: dict) -> str:
    source = _cell_source(cell).strip()
    return "" if source == _PLACEHOLDER_SOURCE else source


def extract_answers(notebook_path: Path) -> ExtractionResult:
    """Read every `aitana:answer`-tagged cell, plus the untagged editable
    cells following an `aitana:question` cell -- see the module docstring."""
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
    # markdown cell both answering question 1, or untagged cells following
    # it) -- concatenate them in notebook order rather than keeping only the
    # first or last match.
    parts_by_field: dict[str, list[str]] = {}
    untagged_cells: dict[str, int] = {}
    # id of the `aitana:question` region we're in, if any
    current_question: str | None = None
    for index, cell in enumerate(notebook.get("cells", [])):
        metadata = cell.get("metadata") or {}
        tags = metadata.get("tags") or []
        field = _aitana_id(metadata)

        if _TAG_QUESTION in tags:
            if field is None:
                logger.warning("Question cell #%d (%s) has no aitana.id metadata", index, cell.get("id"))
            current_question = field
            continue

        if _TAG_ANSWER in tags:
            if field is not None:
                parts_by_field.setdefault(field, []).append(_answer_text(cell))
                continue
            logger.warning("Answer cell #%d (%s) has invalid aitana metadata", index, cell.get("id"))
            # ...and falls through: inside a question region it's still
            # picked up below, as an untagged cell.

        if metadata.get("editable", True) is False:
            current_question = None
            continue
        if current_question is None:
            continue

        source = _answer_text(cell)
        if not source:
            continue
        logger.info(
            "Untagged %s cell #%d (%s) in %s taken as part of the answer to %s",
            cell.get("cell_type"),
            index,
            cell.get("id"),
            notebook_path,
            current_question,
        )
        parts_by_field.setdefault(current_question, []).append(source)
        untagged_cells[current_question] = untagged_cells.get(current_question, 0) + 1

    # Drop blank/placeholder parts before joining so they leave no stray
    # separators; a field whose parts are all blank still maps to "".
    answers = {field: "\n\n".join(p for p in parts if p) for field, parts in parts_by_field.items()}
    non_blank = sum(1 for v in answers.values() if v)
    logger.info(
        "Extracted %d answer group(s) from %s (%d non-blank, %d untagged cell(s))",
        len(answers),
        notebook_path,
        non_blank,
        sum(untagged_cells.values()),
    )
    logger.debug("Fields: %s", list(answers.keys()))
    return ExtractionResult(answers=answers, untagged_cells=untagged_cells)
