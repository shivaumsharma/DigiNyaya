"""SQLite persistence: durable cases + an append-only event log (audit trail).

Replaces the volatile in-memory dict. The events table is append-only and is
both the audit record (essential for a justice system) and the source for
replay-on-refresh. Access is guarded by a lock since jobs and request handlers
touch it from different threads.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

_DB_PATH = os.getenv("DIGINYAYA_DB", os.path.join(os.path.dirname(__file__), "..", "diginyaya.db"))
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    with _lock:
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
        conn.commit()


# ----------------------------- cases ----------------------------- #
def save_case(case: dict) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO cases(case_id, owner_id, data, created_at, updated_at) "
            "VALUES(?,?,?,?,datetime('now'))",
            (case["case_id"], case.get("owner_id", ""), json.dumps(case), case.get("created_at", "")),
        )
        conn.commit()


def get_case(case_id: str) -> Optional[dict]:
    with _lock:
        row = _connect().execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def update_case(case_id: str, **fields) -> Optional[dict]:
    with _lock:
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
    with _lock:
        rows = _connect().execute("SELECT data FROM cases").fetchall()
    return [json.loads(r["data"]) for r in rows]


def case_count() -> int:
    with _lock:
        return _connect().execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]


# ----------------------------- events ----------------------------- #
def append_event(case_id: str, event: dict) -> int:
    with _lock:
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


def get_events(case_id: str, after_seq: int = 0) -> list[dict]:
    with _lock:
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
