"""General feedback: rubrics.general_feedback_instructions, submissions.general_feedback (+ manual override).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('rubrics', sa.Column('general_feedback_instructions', sa.Text(), nullable=True))
    op.add_column('submissions', sa.Column('general_feedback', postgresql.JSONB(), nullable=True))
    op.add_column('submissions', sa.Column('manual_general_feedback', sa.Text(), nullable=True))
    op.add_column('submissions', sa.Column('manual_general_feedback_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('submissions', 'manual_general_feedback_at')
    op.drop_column('submissions', 'manual_general_feedback')
    op.drop_column('submissions', 'general_feedback')
    op.drop_column('rubrics', 'general_feedback_instructions')
