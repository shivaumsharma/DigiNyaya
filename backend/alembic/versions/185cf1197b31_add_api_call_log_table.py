"""add api_call_log table

Revision ID: 185cf1197b31
Revises: 89b8c5988866
Create Date: 2026-08-06 19:16:56.054486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '185cf1197b31'
down_revision: Union[str, Sequence[str], None] = '89b8c5988866'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: autogenerate also proposed dropping cases/documents/discrepancies/
# events -- those live in the SAME sqlite file (see README: "One shared
# SQLite file for cases *and* auth tables") but are created/managed by
# app/db.py's raw sqlite3 code, not tracked in this Base.metadata at all.
# Alembic has no way to know that and would otherwise DESTROY all real case
# data on upgrade. Hand-trimmed to touch only api_call_log.


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('api_call_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('endpoint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('api_call_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_call_log_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_call_log_endpoint'), ['endpoint'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_call_log_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('api_call_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_call_log_user_id'))
        batch_op.drop_index(batch_op.f('ix_api_call_log_endpoint'))
        batch_op.drop_index(batch_op.f('ix_api_call_log_created_at'))

    op.drop_table('api_call_log')
