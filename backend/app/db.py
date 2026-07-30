"""SQLite persistence: durable cases + an append-only event log (audit trail).

Replaces the volatile in-memory dict. The events table is append-only and is
both the audit record (essential for a justice system) and the source for
replay-on-refresh.

Concurrency model: each thread gets its OWN sqlite3.Connection (thread-local,
lazily opened) rather than every thread sharing one connection serialized
behind a single global lock. WAL mode (enabled below) natively supports one
writer + many concurrent readers at the SQLite level -- the old single-lock
design serialized every read behind every write and every other read too,
which is exactly what WAL mode exists to avoid. busy_timeout makes SQLite
retry internally on write contention between threads instead of raising
"database is locked" immediately.

The one place this still needs an explicit lock is update_case()'s
read-modify-write (SELECT the current JSON blob, mutate it, UPDATE it back):
that's two separate statements, so without a lock two threads updating the
SAME case_id could interleave and one update's changes silently overwrite the
other's ("lost update"). Every other function here is a single INSERT/SELECT
statement, which doesn't have that race and needs no lock at all.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

_DB_PATH = os.getenv("DIGINYAYA_DB", os.path.join(os.path.dirname(__file__), "..", "diginyaya.db"))
_local = threading.local()
# Guards ONLY update_case()'s read-modify-write -- see module docstring.
# Every other function here is a single statement and doesn't need this.
_update_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = _connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY,
            owner_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            type TEXT,
            agent TEXT,
            status TEXT,
            title TEXT,
            detail TEXT,
            payload TEXT,
            ts REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id, seq)")
    # No endpoint queries WHERE owner_id=... yet (every lookup today is by
    # case_id, then an in-app ownership check -- see security/auth.py's
    # ensure_owner). Added ahead of a "my disputes" listing endpoint,
    # which doesn't exist yet either; free and harmless to have in place.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            original_filename TEXT,
            storage_path TEXT,
            mime_type TEXT,
            file_size INTEGER,
            is_scanned INTEGER,
            raw_ocr_text TEXT,
            cleaned_text TEXT,
            extraction_status TEXT DEFAULT 'pending',
            ocr_confidence REAL,
            ocr_engine TEXT,
            error_message TEXT,
            uploaded_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS discrepancies (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            document_ids TEXT NOT NULL,
            discrepancy_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            explanation TEXT,
            source_location TEXT,
            flagged_for_review INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancies_case ON discrepancies(case_id)")
    conn.commit()


# ----------------------------- cases ----------------------------- #
def save_case(case: dict) -> None:
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO cases(case_id, owner_id, data, created_at, updated_at) "
        "VALUES(?,?,?,?,datetime('now'))",
        (case["case_id"], case.get("owner_id", ""), json.dumps(case), case.get("created_at", "")),
    )
    conn.commit()


def get_case(case_id: str) -> Optional[dict]:
    row = _connect().execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def update_case(case_id: str, **fields) -> Optional[dict]:
    # See module docstring: the only read-modify-write here, so it's the one
    # place that still needs a lock to stay atomic across threads.
    with _update_lock:
        conn = _connect()
        row = conn.execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            return None
        case = json.loads(row["data"])
        case.update(fields)
        conn.execute(
            "UPDATE cases SET data=?, owner_id=?, updated_at=datetime('now') WHERE case_id=?",
            (json.dumps(case), case.get("owner_id", ""), case_id),
        )
        conn.commit()
        return case


def all_cases() -> list[dict]:
    rows = _connect().execute("SELECT data FROM cases").fetchall()
    return [json.loads(r["data"]) for r in rows]


def case_count() -> int:
    return _connect().execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]


# ----------------------------- events ----------------------------- #
def append_event(case_id: str, event: dict) -> int:
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO events(case_id, type, agent, status, title, detail, payload, ts) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            case_id,
            event.get("type", ""),
            event.get("agent", ""),
            event.get("status", ""),
            event.get("title", ""),
            event.get("detail", ""),
            json.dumps(event.get("payload", {})),
            event.get("ts", 0.0),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ----------------------------- documents ----------------------------- #
def insert_document(doc: dict) -> None:
    conn = _connect()
    conn.execute(
        """INSERT INTO documents(
            id, case_id, original_filename, storage_path, mime_type, file_size,
            is_scanned, raw_ocr_text, cleaned_text, extraction_status,
            ocr_confidence, ocr_engine, error_message
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            doc["id"],
            doc["case_id"],
            doc.get("original_filename"),
            doc.get("storage_path"),
            doc.get("mime_type"),
            doc.get("file_size"),
            int(bool(doc.get("is_scanned", False))),
            doc.get("raw_ocr_text"),
            doc.get("cleaned_text"),
            doc.get("extraction_status", "pending"),
            doc.get("ocr_confidence"),
            doc.get("ocr_engine"),
            doc.get("error_message"),
        ),
    )
    conn.commit()


def _row_to_document(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["is_scanned"] = bool(d["is_scanned"])
    return d


def get_document(document_id: str) -> Optional[dict]:
    row = _connect().execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    return _row_to_document(row) if row else None


def list_documents(case_id: str) -> list[dict]:
    rows = _connect().execute(
        "SELECT * FROM documents WHERE case_id=? ORDER BY uploaded_at", (case_id,)
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
    # Same read-modify-write shape as update_case, and the same fix: each
    # document_id is only ever updated by the one job thread processing it
    # (app.jobs's per-document claim registry already prevents two threads
    # extracting the same document concurrently), but the lock costs nothing
    # here and removes any doubt.
    with _update_lock:
        conn = _connect()
        set_clause = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
        values = list(fields.values())
        if "is_scanned" in fields:
            idx = list(fields).index("is_scanned")
            values[idx] = int(bool(values[idx]))
        conn.execute(f"UPDATE documents SET {set_clause} WHERE id=?", (*values, document_id))
        conn.commit()
    return get_document(document_id)


# ----------------------------- discrepancies ----------------------------- #
def insert_discrepancy(disc: dict) -> None:
    conn = _connect()
    conn.execute(
        """INSERT INTO discrepancies(
            id, case_id, document_ids, discrepancy_type, severity,
            confidence_score, explanation, source_location, flagged_for_review
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            disc["id"],
            disc["case_id"],
            json.dumps(disc.get("document_ids", [])),
            disc["discrepancy_type"],
            disc["severity"],
            disc["confidence_score"],
            disc.get("explanation"),
            disc.get("source_location"),
            int(bool(disc.get("flagged_for_review", False))),
        ),
    )
    conn.commit()


def list_discrepancies(case_id: str) -> list[dict]:
    rows = _connect().execute(
        "SELECT * FROM discrepancies WHERE case_id=? ORDER BY created_at", (case_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["document_ids"] = json.loads(d["document_ids"] or "[]")
        d["flagged_for_review"] = bool(d["flagged_for_review"])
        out.append(d)
    return out


def get_events(case_id: str, after_seq: int = 0) -> list[dict]:
    rows = _connect().execute(
        "SELECT * FROM events WHERE case_id=? AND seq>? ORDER BY seq", (case_id, after_seq)
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "seq": r["seq"],
                "type": r["type"],
                "agent": r["agent"],
                "status": r["status"],
                "title": r["title"],
                "detail": r["detail"],
                "payload": json.loads(r["payload"] or "{}"),
                "ts": r["ts"],
            }
        )
    return out
