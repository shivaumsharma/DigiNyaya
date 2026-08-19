"""drop timezone=True from early auth-table datetime columns

Revision ID: 4f2b9a7c1e3d
Revises: 185cf1197b31
Create Date: 2026-08-13 00:00:00.000000

The very first auth migration (8b8d130c661a) declared login_attempts,
otp_codes, users, and refresh_tokens' datetime columns as
DateTime(timezone=True); every migration since (auth_tokens, api_call_log)
uses plain DateTime(). On SQLite this was invisible either way -- SQLite has
no native tz-aware type, so both forms round-trip as naive regardless (see
app/auth/db.py's utcnow() docstring, which is why every timestamp in this
subsystem is deliberately kept naive UTC in Python). On real Postgres the
difference is real: DateTime(timezone=True) becomes `timestamptz` and
genuinely returns timezone-aware datetimes on read, which breaks the
naive-everywhere convention utcnow() depends on (comparing a naive value
against a freshly-read aware one raises TypeError). Aligning these four
tables with the rest -- plain DateTime(), naive on both dialects -- fixes it
without touching any Python code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f2b9a7c1e3d'
down_revision: Union[str, Sequence[str], None] = '185cf1197b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.alter_column('created_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))

    with op.batch_alter_table('otp_codes', schema=None) as batch_op:
        batch_op.alter_column('expires_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('consumed_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('created_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('email_verified_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('phone_verified_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('created_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('updated_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))

    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.alter_column('expires_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('revoked_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))
        batch_op.alter_column('created_at', type_=sa.DateTime(), existing_type=sa.DateTime(timezone=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.alter_column('created_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('revoked_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('expires_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('updated_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('created_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('phone_verified_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('email_verified_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())

    with op.batch_alter_table('otp_codes', schema=None) as batch_op:
        batch_op.alter_column('created_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('consumed_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
        batch_op.alter_column('expires_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())

    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.alter_column('created_at', type_=sa.DateTime(timezone=True), existing_type=sa.DateTime())
