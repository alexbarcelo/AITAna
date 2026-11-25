"""Initial schema (1.0).

Revision ID: 0001
Revises:
Create Date: 2026-11-25 16:37:44.425645
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('courses',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('editions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('llm_models',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('reasoning_effort', sa.String(length=16), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'model', name='uq_llm_models_provider_model')
    )
    op.create_index('uq_llm_models_single_default', 'llm_models', ['is_default'], unique=True, postgresql_where=sa.text('is_default'))
    op.create_table('students',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('student_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('username', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('student_id')
    )
    op.create_table('favorite_llm_models',
    sa.Column('user_key', sa.String(length=64), nullable=False),
    sa.Column('llm_model_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['llm_model_id'], ['llm_models.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_key', 'llm_model_id')
    )
    op.create_table('rubrics',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('course_id', sa.BigInteger(), nullable=False),
    sa.Column('edition_id', sa.BigInteger(), nullable=True),
    sa.Column('format', sa.Enum('pdf_form', 'notebook', 'generic_document', name='submissionformat', native_enum=False, length=32), nullable=False),
    sa.Column('grading_scale', postgresql.JSONB(), nullable=False),
    sa.Column('context', sa.Text(), nullable=True),
    sa.Column('questions', postgresql.JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['edition_id'], ['editions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_index(op.f('ix_rubrics_course_id'), 'rubrics', ['course_id'], unique=False)
    op.create_index(op.f('ix_rubrics_edition_id'), 'rubrics', ['edition_id'], unique=False)
    op.create_table('batches',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('rubric_id', sa.BigInteger(), nullable=False),
    sa.Column('edition_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.Enum('atenea', name='batchtype', native_enum=False, length=32), nullable=False),
    sa.Column('original_filename', sa.Text(), nullable=True),
    sa.Column('item_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['edition_id'], ['editions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['rubric_id'], ['rubrics.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_batches_edition_id'), 'batches', ['edition_id'], unique=False)
    op.create_index(op.f('ix_batches_rubric_id'), 'batches', ['rubric_id'], unique=False)
    op.create_table('submissions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('student_id', sa.BigInteger(), nullable=True),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('rubric_id', sa.BigInteger(), nullable=False),
    sa.Column('edition_id', sa.BigInteger(), nullable=False),
    sa.Column('file_object_key', sa.Text(), nullable=False),
    sa.Column('original_filename', sa.Text(), nullable=True),
    sa.Column('original_object_key', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'converting', 'segmenting', 'awaiting_segmentation', 'extracting', 'grading', 'graded', 'failed', name='submissionstatus', native_enum=False, length=32), nullable=False),
    sa.Column('answers', postgresql.JSONB(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('batch_id', sa.BigInteger(), nullable=True),
    sa.Column('batch_internal_id', sa.Text(), nullable=True),
    sa.Column('document', postgresql.JSONB(), nullable=True),
    sa.Column('segments', postgresql.JSONB(), nullable=True),
    sa.Column('segmentation_source', sa.Enum('llm', 'manual', name='segmentationsource', native_enum=False, length=32), nullable=True),
    sa.Column('segmented_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['edition_id'], ['editions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['rubric_id'], ['rubrics.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_submissions_batch_id'), 'submissions', ['batch_id'], unique=False)
    op.create_index(op.f('ix_submissions_edition_id'), 'submissions', ['edition_id'], unique=False)
    op.create_index(op.f('ix_submissions_rubric_id'), 'submissions', ['rubric_id'], unique=False)
    op.create_index('ix_submissions_status', 'submissions', ['status'], unique=False)
    op.create_index(op.f('ix_submissions_student_id'), 'submissions', ['student_id'], unique=False)
    op.create_table('grading_traces',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('submission_id', sa.BigInteger(), nullable=False),
    sa.Column('question_id', sa.Text(), nullable=False),
    sa.Column('trace', postgresql.JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_grading_traces_created_at'), 'grading_traces', ['created_at'], unique=False)
    op.create_index('ix_grading_traces_submission_question', 'grading_traces', ['submission_id', 'question_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_grading_traces_submission_question', table_name='grading_traces')
    op.drop_index(op.f('ix_grading_traces_created_at'), table_name='grading_traces')
    op.drop_table('grading_traces')
    op.drop_index(op.f('ix_submissions_student_id'), table_name='submissions')
    op.drop_index('ix_submissions_status', table_name='submissions')
    op.drop_index(op.f('ix_submissions_rubric_id'), table_name='submissions')
    op.drop_index(op.f('ix_submissions_edition_id'), table_name='submissions')
    op.drop_index(op.f('ix_submissions_batch_id'), table_name='submissions')
    op.drop_table('submissions')
    op.drop_index(op.f('ix_batches_rubric_id'), table_name='batches')
    op.drop_index(op.f('ix_batches_edition_id'), table_name='batches')
    op.drop_table('batches')
    op.drop_index(op.f('ix_rubrics_edition_id'), table_name='rubrics')
    op.drop_index(op.f('ix_rubrics_course_id'), table_name='rubrics')
    op.drop_table('rubrics')
    op.drop_table('favorite_llm_models')
    op.drop_table('students')
    op.drop_index('uq_llm_models_single_default', table_name='llm_models', postgresql_where=sa.text('is_default'))
    op.drop_table('llm_models')
    op.drop_table('editions')
    op.drop_table('courses')
