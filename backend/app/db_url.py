"""Single source of truth for resolving DIGINYAYA_DB into a SQLAlchemy URL.

Three places need this (app/db.py, app/auth/db.py, alembic/env.py) and used to
each carry their own copy -- alembic/env.py's copy didn't handle a full URL at
all (see git history), which meant pointing DIGINYAYA_DB at a real Postgres
connection string would silently get mangled into `sqlite:///postgresql://...`
the moment a migration ran. One function, three callers, no drift.
"""

from __future__ import annotations


def resolve_db_url(raw: str) -> str:
    """A value already containing "://" is a full SQLAlchemy URL (e.g.
    postgresql+psycopg://...) and is used as-is. Anything else is treated as
    a bare sqlite file path, matching every caller's historical behavior
    before Postgres was an option.
    """
    if "://" in raw:
        return raw
    return f"sqlite:///{raw}"
