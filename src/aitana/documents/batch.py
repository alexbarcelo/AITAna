from datetime import UTC, datetime
from enum import Enum

from beanie import Document, Link
from pydantic import Field

from .edition import Edition
from .rubric import Rubric


class BatchType(str, Enum):
    """Which zip layout a batch upload follows -- only one today (Atenea,
    UPC's Moodle instance's assignment-submission export). Same shape as
    `StudentImportFormat`/`_IMPORT_PARSERS` in `api/routers/students.py`:
    add a member here plus a matching parser registered in
    `api/routers/batches.py`'s `_BATCH_PARSERS` for another."""

    ATENEA = "atenea"


class Batch(Document):
    """One batch-upload event: a zip containing one subfolder per student
    submission, all against the same rubric. `rubric` (implying the course,
    same as `Submission` never storing course directly) is required; `edition`
    is required too, resolved the same way a single submission's edition is
    (the rubric's own edition if it has one, else the uploader's explicit
    choice). See `Submission.batch`/`.batch_internal_id` for how the
    individual extracted items relate back to this."""

    rubric: Link[Rubric]
    edition: Link[Edition]
    type: BatchType
    original_filename: str | None = None
    item_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "batches"
