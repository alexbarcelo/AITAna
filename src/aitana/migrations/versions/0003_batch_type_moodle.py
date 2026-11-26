"""Rename the `atenea` batch type to `moodle`.

The zip layout is Moodle's own per-assignment export; Atenea is just UPC's
Moodle instance. `batches.type` is a plain VARCHAR (no native enum, no CHECK
constraint), so only the stored values change -- but they must, since the
ORM refuses to load a value its `BatchType` enum no longer has.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE batches SET type = 'moodle' WHERE type = 'atenea'")


def downgrade() -> None:
    op.execute("UPDATE batches SET type = 'atenea' WHERE type = 'moodle'")
