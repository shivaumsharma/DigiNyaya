"""create case management tables (cases, events, documents, discrepancies)

Revision ID: 7c1d94e6a2f0
Revises: 4f2b9a7c1e3d
Create Date: 2026-08-13 00:10:00.000000

These four tables were previously created ad-hoc by app/db.py's raw sqlite3
`CREATE TABLE IF NOT EXISTS` calls (init_db()), entirely outside Alembic --
including an ad-hoc "add hash/prev_hash columns if missing" migration
(_ensure_events_hash_columns) substituting for a real migration. Now that
app/db.py has been rewritten to SQLAlchemy Core against the same Base.metadata
as the auth tables, this is the natural point to bring them under Alembic too
-- a real Postgres deployment gets its schema from `alembic upgrade head`
(which the Docker CMD already runs before uvicorn starts), while
app/db.py's init_db() call keeps calling metadata.create_all() for local/dev
convenience -- idempotent, so it's a no-op wherever this migration already
ran.

No data migration step: production SQLite is wiped on every Render redeploy
already (see README's Known Issues), so there's no precious data behind this
schema to carry forward -- a fresh create is the reasonable starting point.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c1d94e6a2f0'
down_revision: Union[str, Sequence[str], None] = '4f2b9a7c1e3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'cases',
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('owner_id', sa.String(), nullable=True),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('case_id'),
    )
    op.create_index('idx_cases_owner', 'cases', ['owner_id'], unique=False)

    op.create_table(
        'events',
        sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('type', sa.Text(), nullable=True),
        sa.Column('agent', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('ts', sa.Float(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=True),
        sa.Column('hash', sa.Text(), nullable=True),
        sa.Column('prev_hash', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('seq'),
    )
    op.create_index('idx_events_case', 'events', ['case_id', 'seq'], unique=False)

    op.create_table(
        'documents',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('original_filename', sa.Text(), nullable=True),
        sa.Column('storage_path', sa.Text(), nullable=True),
        sa.Column('mime_type', sa.Text(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('is_scanned', sa.Boolean(), nullable=True),
        sa.Column('raw_ocr_text', sa.Text(), nullable=True),
        sa.Column('cleaned_text', sa.Text(), nullable=True),
        sa.Column('extraction_status', sa.Text(), server_default='pending', nullable=True),
        sa.Column('ocr_confidence', sa.Float(), nullable=True),
        sa.Column('ocr_engine', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('uploaded_at', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_documents_case', 'documents', ['case_id'], unique=False)

    op.create_table(
        'discrepancies',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('document_ids', sa.Text(), nullable=False),
        sa.Column('discrepancy_type', sa.Text(), nullable=False),
        sa.Column('severity', sa.Text(), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('source_location', sa.Text(), nullable=True),
        sa.Column('flagged_for_review', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_discrepancies_case', 'discrepancies', ['case_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_discrepancies_case', table_name='discrepancies')
    op.drop_table('discrepancies')

    op.drop_index('idx_documents_case', table_name='documents')
    op.drop_table('documents')

    op.drop_index('idx_events_case', table_name='events')
    op.drop_table('events')

    op.drop_index('idx_cases_owner', table_name='cases')
    op.drop_table('cases')
