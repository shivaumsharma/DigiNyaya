"""Postgres/SQLite-portable persistence via SQLAlchemy Core: durable cases +
an append-only event log (audit trail).

Replaces the volatile in-memory dict. The events table is append-only and is
both the audit record (essential for a justice system) and the source for
replay-on-refresh.

Rewritten from raw sqlite3 (2026-08-08) to SQLAlchemy Core so the same code
runs against a real Postgres instance (DIGINYAYA_DB=postgresql+psycopg://...)
as well as local/test SQLite -- see backend/app/db_url.py for how that env
var is resolved, and the AWS migration plan for why. Kept as Core rather than
a full ORM rewrite: the schema is already parameterized SQL against named
tables/columns, which maps onto Table/select/insert/update almost 1:1
without forcing every row into a mapped class.

Concurrency model: SQLAlchemy's connection pool replaces the old thread-
local sqlite3.Connection cache -- each call below opens a short-lived pooled
connection via engine.begin() rather than reusing one per thread. WAL mode
(enabled below, sqlite only) still gives one writer + many concurrent
readers at the SQLite level. On Postgres, MVCC provides the same property
natively and neither WAL nor busy_timeout apply.

The one place this still needs an explicit lock is update_case()'s
read-modify-write (SELECT the current JSON blob, mutate it, UPDATE it back):
that's two separate statements, so without a lock two threads updating the
SAME case_id could interleave and one update's changes silently overwrite the
other's ("lost update"). This is a Python-process-level race (uvicorn's
single worker + jobs.py's background threads), not a database one, so the
same threading.Lock is correct regardless of which database engine is behind
it -- this migration deliberately runs exactly one backend instance (see the
AWS migration plan), so a single in-process lock is still sufficient. Every
other function here is a single INSERT/SELECT statement, which doesn't have
that race and needs no lock at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Table,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.pool import StaticPool

from .auth.db import Base
from .db_url import resolve_db_url

_DB_PATH = os.getenv("DIGINYAYA_DB", os.path.join(os.path.dirname(__file__), "..", "diginyaya.db"))
_DATABASE_URL = resolve_db_url(_DB_PATH)

_is_sqlite = _DATABASE_URL.startswith("sqlite")
_is_sqlite_memory = _is_sqlite and (":memory:" in _DATABASE_URL or _DATABASE_URL == "sqlite://")

_engine_kwargs: dict = {"connect_args": {"check_same_thread": False}} if _is_sqlite else {}
if _is_sqlite_memory:
    # A plain sqlite:///:memory: engine hands each new pooled connection its
    # OWN fresh, empty database -- fine for sqlite3's single persistent
    # connection this file used to keep, but SQLAlchemy's pool opens/closes
    # connections per checkout by default, so without StaticPool (one
    # connection, reused, never closed) every call would see an empty DB.
    # Only tests use ":memory:" (see tests/conftest.py's DIGINYAYA_DB=":memory:").
    _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(_DATABASE_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# Shared with app/auth/db.py's ORM models (Base.metadata) so both subsystems'
# tables live on one MetaData -- the one alembic/env.py points target_metadata
# at, keeping every table (auth or case-management) visible to migrations.
metadata = Base.metadata

cases = Table(
    "cases",
    metadata,
    Column("case_id", String, primary_key=True),
    Column("owner_id", String),
    Column("data", Text, nullable=False),
    Column("created_at", Text),
    Column("updated_at", Text),
)

events = Table(
    "events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String, nullable=False),
    Column("type", Text),
    Column("agent", Text),
    Column("status", Text),
    Column("title", Text),
    Column("detail", Text),
    Column("payload", Text),
    Column("ts", Float),
    Column("created_at", Text),
    Column("hash", Text),
    Column("prev_hash", Text),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String, primary_key=True),
    Column("case_id", String, nullable=False),
    Column("original_filename", Text),
    Column("storage_path", Text),
    Column("mime_type", Text),
    Column("file_size", Integer),
    Column("is_scanned", Boolean),
    Column("raw_ocr_text", Text),
    Column("cleaned_text", Text),
    Column("extraction_status", Text, server_default="pending"),
    Column("ocr_confidence", Float),
    Column("ocr_engine", Text),
    Column("error_message", Text),
    Column("uploaded_at", Text),
    Column("updated_at", Text),
)

discrepancies = Table(
    "discrepancies",
    metadata,
    Column("id", String, primary_key=True),
    Column("case_id", String, nullable=False),
    Column("document_ids", Text, nullable=False),
    Column("discrepancy_type", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("confidence_score", Float, nullable=False),
    Column("explanation", Text),
    Column("source_location", Text),
    Column("flagged_for_review", Boolean, nullable=False, default=False, server_default="0"),
    Column("created_at", Text),
)

# Guards ONLY update_case()'s read-modify-write -- see module docstring.
# Every other function here is a single statement and doesn't need this.
_update_lock = threading.Lock()
# Guards append_event()'s read-then-write (look up the case's last event
# hash, then insert a row chained to it) -- same race as _update_lock's, on
# a different table: two threads appending to the SAME case concurrently
# (routine during a pipeline run -- several agents emit events in flight)
# could otherwise both read the same "last hash" and each insert a row
# claiming to follow it, breaking the one-linear-chain-per-case invariant
# verify_case_events() relies on.
_event_lock = threading.Lock()

# Sentinel prev_hash for a case's first hashed event, and for the event
# immediately following an unhashed "legacy" event (see verify_case_events()
# below) -- distinguishes "nothing to chain from yet" from a real prior hash
# without needing a nullable comparison at every verification step.
_EVENT_CHAIN_GENESIS = "0" * 64


def _now_iso() -> str:
    """Python-computed timestamp string for the free-text created_at/
    updated_at columns above (they're Text, not a typed DateTime -- nothing
    else in the app parses them, see the AWS migration plan). Computed in
    Python rather than via a DB-side now()/CURRENT_TIMESTAMP function so the
    stored format is identical regardless of which database is behind this
    engine.
    """
    return datetime.now(timezone.utc).isoformat()


def _compute_event_hash(case_id: str, event_data: dict, ts: float, prev_hash: str) -> str:
    """Deterministic hash covering everything append_event() persists for
    this row plus the previous event's hash, forming a per-case hash chain:
    changing any stored field of ANY past event, or removing/reordering one,
    changes every hash computed after it, which verify_case_events() can
    detect. sort_keys makes the encoding independent of dict insertion order.
    """
    canonical = json.dumps(
        {
            "case_id": case_id,
            "type": event_data.get("type", ""),
            "agent": event_data.get("agent", ""),
            "status": event_data.get("status", ""),
            "title": event_data.get("title", ""),
            "detail": event_data.get("detail", ""),
            "payload": event_data.get("payload", {}),
            "ts": ts,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def init_db() -> None:
    # create_all is idempotent (CREATE TABLE IF NOT EXISTS-equivalent per
    # dialect) -- schema evolution from here on happens via Alembic
    # migrations (see alembic/versions/), not by editing the Table defs
    # above and relying on this call to pick up the change.
    metadata.create_all(engine, tables=[cases, events, documents, discrepancies])


# ----------------------------- cases ----------------------------- #
def save_case(case: dict) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    values = {
        "case_id": case["case_id"],
        "owner_id": case.get("owner_id", ""),
        "data": json.dumps(case),
        "created_at": case.get("created_at", ""),
        "updated_at": _now_iso(),
    }
    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt = insert_fn(cases).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["case_id"],
        set_={k: v for k, v in values.items() if k != "case_id"},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def get_case(case_id: str) -> Optional[dict]:
    with engine.begin() as conn:
        row = conn.execute(select(cases.c.data).where(cases.c.case_id == case_id)).fetchone()
    return json.loads(row.data) if row else None


def update_case(case_id: str, **fields) -> Optional[dict]:
    # See module docstring: the only read-modify-write here, so it's the one
    # place that still needs a lock to stay atomic across threads.
    with _update_lock, engine.begin() as conn:
        row = conn.execute(select(cases.c.data).where(cases.c.case_id == case_id)).fetchone()
        if row is None:
            return None
        case = json.loads(row.data)
        case.update(fields)
        conn.execute(
            cases.update()
            .where(cases.c.case_id == case_id)
            .values(data=json.dumps(case), owner_id=case.get("owner_id", ""), updated_at=_now_iso())
        )
        return case


def all_cases() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(select(cases.c.data)).fetchall()
    return [json.loads(r.data) for r in rows]


def list_cases_by_owner(owner_id: str) -> list[dict]:
    # idx_cases_owner already existed (added ahead of this endpoint, see its
    # own comment) -- this is the first query that actually uses it.
    with engine.begin() as conn:
        rows = conn.execute(
            select(cases.c.data).where(cases.c.owner_id == owner_id).order_by(cases.c.created_at.desc())
        ).fetchall()
    return [json.loads(r.data) for r in rows]


def case_count() -> int:
    with engine.begin() as conn:
        return conn.execute(select(func.count()).select_from(cases)).scalar_one()


# ----------------------------- events ----------------------------- #
def append_event(case_id: str, event_data: dict) -> int:
    ts = event_data.get("ts", 0.0)
    # See _event_lock's declaration: the read (last hash for this case) and
    # the write (insert chained to it) must be atomic together, or two
    # threads appending to the same case could both chain off the same
    # prior event.
    with _event_lock, engine.begin() as conn:
        last = conn.execute(
            select(events.c.hash).where(events.c.case_id == case_id).order_by(events.c.seq.desc()).limit(1)
        ).fetchone()
        # A NULL hash means the last row predates hashing -- start a fresh
        # chain from genesis rather than chaining onto a hash that was never
        # computed.
        prev_hash = last.hash if (last and last.hash) else _EVENT_CHAIN_GENESIS
        event_hash = _compute_event_hash(case_id, event_data, ts, prev_hash)
        result = conn.execute(
            events.insert().values(
                case_id=case_id,
                type=event_data.get("type", ""),
                agent=event_data.get("agent", ""),
                status=event_data.get("status", ""),
                title=event_data.get("title", ""),
                detail=event_data.get("detail", ""),
                payload=json.dumps(event_data.get("payload", {})),
                ts=ts,
                created_at=_now_iso(),
                hash=event_hash,
                prev_hash=prev_hash,
            )
        )
        return result.inserted_primary_key[0]


def verify_case_events(case_id: str) -> dict:
    """Recompute this case's event hash chain and report whether it's
    intact. Events with no hash (inserted before the tamper-evident audit
    log existed) are counted separately as "unverifiable" rather than
    treated as broken links -- append_event() deliberately restarts the
    chain at genesis right after one, so a legacy gap is expected, not
    evidence of tampering.

    Returns {"verified": bool, "event_count": int, "verified_count": int,
    "unverifiable_count": int, "first_break_seq": int | None}. verified is
    True only if every hashed event's stored hash matches a fresh
    recomputation AND correctly chains to the event before it.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                events.c.seq,
                events.c.case_id,
                events.c.type,
                events.c.agent,
                events.c.status,
                events.c.title,
                events.c.detail,
                events.c.payload,
                events.c.ts,
                events.c.hash,
                events.c.prev_hash,
            )
            .where(events.c.case_id == case_id)
            .order_by(events.c.seq)
        ).fetchall()

    expected_prev = _EVENT_CHAIN_GENESIS
    unverifiable_count = 0
    verified_count = 0
    first_break_seq: Optional[int] = None

    for row in rows:
        if not row.hash:
            unverifiable_count += 1
            expected_prev = _EVENT_CHAIN_GENESIS  # chain restarts after a legacy gap
            continue

        event_data = {
            "type": row.type,
            "agent": row.agent,
            "status": row.status,
            "title": row.title,
            "detail": row.detail,
            "payload": json.loads(row.payload or "{}"),
        }
        recomputed = _compute_event_hash(row.case_id, event_data, row.ts, expected_prev)
        broken_here = row.prev_hash != expected_prev or row.hash != recomputed
        if broken_here and first_break_seq is None:
            first_break_seq = row.seq
        # Once any row's hash is untrustworthy, every row chained after it
        # is unverifiable too, even if its own prev_hash/hash still happen
        # to line up against that already-suspect value -- a stored hash
        # column is not re-derived here, so a tamper that edits a field
        # without also recomputing that row's hash doesn't corrupt what
        # later rows compare against; trust still shouldn't propagate past
        # the point where the chain was shown to have been forged or edited.
        if first_break_seq is None:
            verified_count += 1
        expected_prev = row.hash

    return {
        "verified": first_break_seq is None,
        "event_count": len(rows),
        "verified_count": verified_count,
        "unverifiable_count": unverifiable_count,
        "first_break_seq": first_break_seq,
    }


# ----------------------------- documents ----------------------------- #
def insert_document(doc: dict) -> None:
    now = _now_iso()
    with engine.begin() as conn:
        conn.execute(
            documents.insert().values(
                id=doc["id"],
                case_id=doc["case_id"],
                original_filename=doc.get("original_filename"),
                storage_path=doc.get("storage_path"),
                mime_type=doc.get("mime_type"),
                file_size=doc.get("file_size"),
                is_scanned=bool(doc.get("is_scanned", False)),
                raw_ocr_text=doc.get("raw_ocr_text"),
                cleaned_text=doc.get("cleaned_text"),
                extraction_status=doc.get("extraction_status", "pending"),
                ocr_confidence=doc.get("ocr_confidence"),
                ocr_engine=doc.get("ocr_engine"),
                error_message=doc.get("error_message"),
                uploaded_at=now,
                updated_at=now,
            )
        )


def _row_to_document(row) -> dict:
    return dict(row._mapping)


def get_document(document_id: str) -> Optional[dict]:
    with engine.begin() as conn:
        row = conn.execute(select(documents).where(documents.c.id == document_id)).fetchone()
    return _row_to_document(row) if row else None


def list_documents(case_id: str) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            select(documents).where(documents.c.case_id == case_id).order_by(documents.c.uploaded_at)
        ).fetchall()
    return [_row_to_document(r) for r in rows]


_DOCUMENT_COLUMNS = {
    "original_filename", "storage_path", "mime_type", "file_size", "is_scanned",
    "raw_ocr_text", "cleaned_text", "extraction_status", "ocr_confidence",
    "ocr_engine", "error_message",
}


def update_document(document_id: str, **fields) -> Optional[dict]:
    unknown = set(fields) - _DOCUMENT_COLUMNS
    if unknown:
        raise ValueError(f"update_document got unknown field(s): {unknown}")
    if not fields:
        return get_document(document_id)
    if "is_scanned" in fields:
        fields = {**fields, "is_scanned": bool(fields["is_scanned"])}
    # Same read-modify-write shape as update_case, and the same fix: each
    # document_id is only ever updated by the one job thread processing it
    # (app.jobs's per-document claim registry already prevents two threads
    # extracting the same document concurrently), but the lock costs nothing
    # here and removes any doubt.
    with _update_lock, engine.begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == document_id)
            .values(updated_at=_now_iso(), **fields)
        )
    return get_document(document_id)


# ----------------------------- discrepancies ----------------------------- #
def insert_discrepancy(disc: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            discrepancies.insert().values(
                id=disc["id"],
                case_id=disc["case_id"],
                document_ids=json.dumps(disc.get("document_ids", [])),
                discrepancy_type=disc["discrepancy_type"],
                severity=disc["severity"],
                confidence_score=disc["confidence_score"],
                explanation=disc.get("explanation"),
                source_location=disc.get("source_location"),
                flagged_for_review=bool(disc.get("flagged_for_review", False)),
                created_at=_now_iso(),
            )
        )


def list_discrepancies(case_id: str) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            select(discrepancies).where(discrepancies.c.case_id == case_id).order_by(discrepancies.c.created_at)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["document_ids"] = json.loads(d["document_ids"] or "[]")
        out.append(d)
    return out


def get_events(case_id: str, after_seq: int = 0) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            select(events).where(events.c.case_id == case_id, events.c.seq > after_seq).order_by(events.c.seq)
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "seq": r.seq,
                "type": r.type,
                "agent": r.agent,
                "status": r.status,
                "title": r.title,
                "detail": r.detail,
                "payload": json.loads(r.payload or "{}"),
                "ts": r.ts,
            }
        )
    return out
