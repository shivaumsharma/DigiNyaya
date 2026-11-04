"""add job_queue table

Revision ID: a3c5e7f9b1d2
Revises: 7c1d94e6a2f0
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, Sequence[str], None] = '7c1d94e6a2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_queue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('run_after', sa.Float(), nullable=False),
        sa.Column('locked_by', sa.String(), nullable=True),
        sa.Column('locked_until', sa.Float(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('finished_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_queue_status', 'job_queue', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_job_queue_status', table_name='job_queue')
    op.drop_table('job_queue')
