"""SQLAlchemy engine/session for the auth subsystem.

Points at the SAME sqlite file app/db.py already uses (via the same
DIGINYAYA_DB env var) so the whole app stays a single deployable .db file --
cases/events keep using the existing hand-rolled sqlite3 access in db.py,
while users/refresh_tokens/otp_codes/login_attempts go through this
SQLAlchemy engine. Two access paths into one file is safe: sqlite3's
WAL mode (already turned on in db.py's _connect()) allows concurrent
readers/writers across connections.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def utcnow() -> datetime:
    """Naive UTC datetime -- SQLite has no native timezone-aware column type,
    and SQLAlchemy's sqlite dialect always returns naive datetimes on read
    regardless of whether the column was declared DateTime(timezone=True).
    Mixing tz-aware Python defaults with naive reads-back-from-DB raises
    "can't compare offset-naive and offset-aware datetimes" the first time
    code compares an in-memory value to one just loaded from a query. Keeping
    every timestamp in this subsystem naive UTC avoids that class of bug.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _sqlalchemy_url(raw: str) -> str:
    """DIGINYAYA_DB has historically always been a raw sqlite file path, but
    needs to become a full connection string once a real Postgres instance
    is configured. A value already containing "://" is used as-is (any
    SQLAlchemy-supported URL, e.g. postgresql://...); anything else is
    treated as a sqlite file path, matching this module's previous
    hardcoded-`sqlite:///` behavior exactly.
    """
    if "://" in raw:
        return raw
    return f"sqlite:///{raw}"


_DB_PATH = os.getenv(
    "DIGINYAYA_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "diginyaya.db"),
)
_DATABASE_URL = _sqlalchemy_url(_DB_PATH)

# check_same_thread is a sqlite3-DBAPI-specific pyfunc arg; passing it to a
# non-sqlite dialect (e.g. psycopg2) raises a TypeError at connect time.
_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(_DATABASE_URL, connect_args=_connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    """FastAPI dependency: yields a session, always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_auth_db() -> None:
    """Create auth tables if they don't exist yet.

    Alembic is the source of truth for schema evolution (see backend/alembic/),
    but this is a harmless no-op safety net for a fresh dev checkout that
    hasn't run migrations yet -- mirrors db.init_db()'s CREATE TABLE IF NOT
    EXISTS pattern for cases/events.
    """
    from . import orm_models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
