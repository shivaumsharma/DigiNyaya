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

import hashlib
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
# Guards append_event()'s read-then-write (look up the case's last event
# hash, then insert a row chained to it) -- same race as _update_lock's, on
# a different table: two threads appending to the SAME case concurrently
# (routine during a pipeline run -- several agents emit events in flight)
# could otherwise both read the same "last hash" and each insert a row
# claiming to follow it, breaking the one-linear-chain-per-case invariant
# verify_case_events() relies on.
_event_lock = threading.Lock()

# Sentinel prev_hash for a case's first hashed event, and for the event
# immediately following an unhashed "legacy" event (see the migration in
# init_db() and verify_case_events() below) -- distinguishes "nothing to
# chain from yet" from a real prior hash without needing a nullable
# comparison at every verification step.
_EVENT_CHAIN_GENESIS = "0" * 64


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def _ensure_events_hash_columns(conn: sqlite3.Connection) -> None:
    """Add hash/prev_hash to an events table created before the tamper-
    evident audit log (added 2026-08-08). SQLite's ADD COLUMN is safe on a
    live table with real rows -- existing rows just get NULL for both new
    columns, which verify_case_events() treats as "predates hashing",
    never as tampering. CREATE TABLE IF NOT EXISTS above is a no-op on an
    existing table, so this is the actual migration path for any database
    that already had an events table before this column existed.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "hash" not in existing:
        conn.execute("ALTER TABLE events ADD COLUMN hash TEXT")
    if "prev_hash" not in existing:
        conn.execute("ALTER TABLE events ADD COLUMN prev_hash TEXT")
    conn.commit()


def _compute_event_hash(case_id: str, event: dict, ts: float, prev_hash: str) -> str:
    """Deterministic hash covering everything append_event() persists for
    this row plus the previous event's hash, forming a per-case hash chain:
    changing any stored field of ANY past event, or removing/reordering one,
    changes every hash computed after it, which verify_case_events() can
    detect. sort_keys makes the encoding independent of dict insertion order.
    """
    canonical = json.dumps(
        {
            "case_id": case_id,
            "type": event.get("type", ""),
            "agent": event.get("agent", ""),
            "status": event.get("status", ""),
            "title": event.get("title", ""),
            "detail": event.get("detail", ""),
            "payload": event.get("payload", {}),
            "ts": ts,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            created_at TEXT DEFAULT (datetime('now')),
            hash TEXT,
            prev_hash TEXT
        )"""
    )
    _ensure_events_hash_columns(conn)
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


def list_cases_by_owner(owner_id: str) -> list[dict]:
    # idx_cases_owner already existed (added ahead of this endpoint, see its
    # own comment) -- this is the first query that actually uses it.
    rows = _connect().execute(
        "SELECT data FROM cases WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)
    ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def case_count() -> int:
    return _connect().execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]


# ----------------------------- events ----------------------------- #
def append_event(case_id: str, event: dict) -> int:
    ts = event.get("ts", 0.0)
    # See _event_lock's declaration: the read (last hash for this case) and
    # the write (insert chained to it) must be atomic together, or two
    # threads appending to the same case could both chain off the same
    # prior event.
    with _event_lock:
        conn = _connect()
        last = conn.execute(
            "SELECT hash FROM events WHERE case_id=? ORDER BY seq DESC LIMIT 1", (case_id,)
        ).fetchone()
        # A NULL hash means the last row predates hashing (see
        # _ensure_events_hash_columns) -- start a fresh chain from genesis
        # rather than chaining onto a hash that was never computed.
        prev_hash = last["hash"] if (last and last["hash"]) else _EVENT_CHAIN_GENESIS
        event_hash = _compute_event_hash(case_id, event, ts, prev_hash)
        cur = conn.execute(
            "INSERT INTO events(case_id, type, agent, status, title, detail, payload, ts, hash, prev_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                case_id,
                event.get("type", ""),
                event.get("agent", ""),
                event.get("status", ""),
                event.get("title", ""),
                event.get("detail", ""),
                json.dumps(event.get("payload", {})),
                ts,
                event_hash,
                prev_hash,
            ),
        )
        conn.commit()
        return cur.lastrowid


def verify_case_events(case_id: str) -> dict:
    """Recompute this case's event hash chain and report whether it's
    intact. Events with no hash (inserted before the migration in
    _ensure_events_hash_columns) are counted separately as "unverifiable"
    rather than treated as broken links -- append_event() deliberately
    restarts the chain at genesis right after one, so a legacy gap is
    expected, not evidence of tampering.

    Returns {"verified": bool, "event_count": int, "verified_count": int,
    "unverifiable_count": int, "first_break_seq": int | None}. verified is
    True only if every hashed event's stored hash matches a fresh
    recomputation AND correctly chains to the event before it.
    """
    rows = _connect().execute(
        "SELECT seq, case_id, type, agent, status, title, detail, payload, ts, hash, prev_hash "
        "FROM events WHERE case_id=? ORDER BY seq",
        (case_id,),
    ).fetchall()

    expected_prev = _EVENT_CHAIN_GENESIS
    unverifiable_count = 0
    verified_count = 0
    first_break_seq: Optional[int] = None

    for row in rows:
        if not row["hash"]:
            unverifiable_count += 1
            expected_prev = _EVENT_CHAIN_GENESIS  # chain restarts after a legacy gap
            continue

        event = {
            "type": row["type"],
            "agent": row["agent"],
            "status": row["status"],
            "title": row["title"],
            "detail": row["detail"],
            "payload": json.loads(row["payload"] or "{}"),
        }
        recomputed = _compute_event_hash(row["case_id"], event, row["ts"], expected_prev)
        broken_here = row["prev_hash"] != expected_prev or row["hash"] != recomputed
        if broken_here and first_break_seq is None:
            first_break_seq = row["seq"]
        # Once any row's hash is untrustworthy, every row chained after it
        # is unverifiable too, even if its own prev_hash/hash still happen
        # to line up against that already-suspect value -- a stored hash
        # column is not re-derived here, so a tamper that edits a field
        # without also recomputing that row's hash doesn't corrupt what
        # later rows compare against; trust still shouldn't propagate past
        # the point where the chain was shown to have been forged or edited.
        if first_break_seq is None:
            verified_count += 1
        expected_prev = row["hash"]

    return {
        "verified": first_break_seq is None,
        "event_count": len(rows),
        "verified_count": verified_count,
        "unverifiable_count": unverifiable_count,
        "first_break_seq": first_break_seq,
    }


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
