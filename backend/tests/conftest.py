"""Test fixtures. Points the auth DB at a scratch sqlite file (never the dev
diginyaya.db) and disables the LLM so tests never make real network calls --
the env vars here MUST be set before `app.main` (and anything it imports)
is first imported, since app/llm/config.py and app/auth/db.py both read
os.environ at import time.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "diginyaya_test_auth.db"
for suffix in ("", "-wal", "-shm"):
    p = Path(str(_TEST_DB) + suffix)
    if p.exists():
        p.unlink()

os.environ["DIGINYAYA_DB"] = str(_TEST_DB)
os.environ["DIGINYAYA_ENV"] = "development"  # keep require_https a no-op for plain http TestClient requests
os.environ["DIGINYAYA_USE_LLM"] = "0"
os.environ["DIGINYAYA_LLM_PROVIDER"] = "mock"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth.db import Base, SessionLocal, engine
from app.auth.orm_models import AuthToken, LoginAttempt, OtpCode, RefreshToken, User
from app.main import app

Base.metadata.create_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_auth_tables():
    """Every test starts against empty auth tables, independent of the
    other test files/db.py's cases table (untouched by any of this).
    """
    yield
    db = SessionLocal()
    try:
        for model in (AuthToken, LoginAttempt, OtpCode, RefreshToken, User):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
