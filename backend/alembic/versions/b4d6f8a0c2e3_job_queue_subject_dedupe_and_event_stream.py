"""job_queue subject and dedupe key, event_stream table

Revision ID: b4d6f8a0c2e3
Revises: a3c5e7f9b1d2
Create Date: 2026-11-05 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d6f8a0c2e3'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('job_queue', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('dedupe_key', sa.String(), nullable=True))
        batch_op.create_index('ix_job_queue_subject', ['subject'], unique=False)
        batch_op.create_unique_constraint('uq_job_queue_dedupe_key', ['dedupe_key'])

    op.create_table(
        'event_stream',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_event_stream_case_id', 'event_stream', ['case_id'], unique=False)
    op.create_index('ix_event_stream_created_at', 'event_stream', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_event_stream_created_at', table_name='event_stream')
    op.drop_index('ix_event_stream_case_id', table_name='event_stream')
    op.drop_table('event_stream')
    with op.batch_alter_table('job_queue', schema=None) as batch_op:
        batch_op.drop_constraint('uq_job_queue_dedupe_key', type_='unique')
        batch_op.drop_index('ix_job_queue_subject')
        batch_op.drop_column('dedupe_key')
        batch_op.drop_column('subject')
