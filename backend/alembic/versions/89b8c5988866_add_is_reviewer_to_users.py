"""add is_reviewer to users

Revision ID: 89b8c5988866
Revises: 2068783f6e90
Create Date: 2026-07-30 22:23:03.932584

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89b8c5988866'
down_revision: Union[str, Sequence[str], None] = '2068783f6e90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: autogenerate also proposed dropping cases/events/documents/
# discrepancies -- those are app.db's hand-rolled sqlite3 tables (a
# separate persistence system, not part of this SQLAlchemy Base's
# metadata), not something this migration should ever touch. Trimmed down
# to just the real change.


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_reviewer', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_reviewer')
