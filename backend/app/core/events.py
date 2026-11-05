"""Events + a live pub/sub bus.

Two channels work together:
  - The append-only event LOG (the `events` table, in db.py) is the durable audit trail and
    powers replay-on-refresh.
  - The BUS streams events (including per-token deltas) live to any currently-connected SSE clients.

A job publishes coarse events to BOTH (so they survive restart and replay) and
high-frequency token events to the bus ONLY (not worth persisting).

Two bus implementations, chosen by DIGINYAYA_EVENT_BUS:
  db      (default) events go through the `event_stream` table, so a worker PROCESS can publish and any number of API
          processes can stream them. Subscribers poll with a short, adaptive interval.
  memory  in-process only; correct only when the publisher and the SSE server are the same process (single-process dev).
Both expose subscribe / unsubscribe / publish and a subscription with `get(timeout)` that raises queue.Empty, so
stream_from_queue works with either. A Redis or Postgres LISTEN/NOTIFY bus can be added behind the same three methods.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import threading
import time
from typing import Iterator

# Event types that terminate a stream.
TERMINAL = {"awaiting_decision", "resolved", "error", "escalated_terminal"}

# Types we persist to the durable log (everything except live tokens).
EPHEMERAL = {"token"}


def make_event(type: str, *, agent: str = "orchestrator", title: str = "", status: str = "", detail: str = "", payload: dict | None = None) -> dict:
    return {
        "type": type,
        "agent": agent,
        "title": title,
        "status": status,
        "detail": detail,
        "payload": payload or {},
        "ts": time.time(),
    }


class EventBus:
    """In-memory fan-out of events per case to live subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, case_id: str) -> "queue.Queue":
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(case_id, []).append(q)
        return q

    def unsubscribe(self, case_id: str, q: "queue.Queue") -> None:
        with self._lock:
            subs = self._subs.get(case_id)
            if subs and q in subs:
                subs.remove(q)
            if subs is not None and not subs:
                self._subs.pop(case_id, None)

    def publish(self, case_id: str, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(case_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # pragma: no cover
                pass


class DbSubscription:
    """A subscriber that reads new rows of `event_stream` for one case. It starts at the newest row existing when it was
    created, so it only sees later events; the durable log covers everything earlier (the SSE endpoint subscribes first,
    then replays the log, so nothing falls in the gap)."""

    POLL_MIN = 0.05
    POLL_MAX = 0.4

    def __init__(self, case_id: str, start_id: int) -> None:
        self.case_id = case_id
        self.last_id = start_id
        self._buffer: collections.deque = collections.deque()
        self._interval = self.POLL_MIN

    def _fetch(self) -> None:
        from sqlalchemy import select

        from .. import db

        t = db.event_stream
        with db.engine.connect() as conn:
            rows = conn.execute(select(t.c.id, t.c.payload).where(t.c.case_id == self.case_id, t.c.id > self.last_id)
                                .order_by(t.c.id).limit(500)).all()
        for row_id, payload in rows:
            self.last_id = row_id
            try:
                self._buffer.append(json.loads(payload))
            except ValueError:
                continue

    def get(self, timeout: float = 1.0) -> dict:
        deadline = time.time() + timeout
        while True:
            if self._buffer:
                self._interval = self.POLL_MIN
                return self._buffer.popleft()
            self._fetch()
            if self._buffer:
                continue
            if time.time() >= deadline:
                raise queue.Empty
            time.sleep(min(self._interval, max(deadline - time.time(), 0.0)))
            self._interval = min(self._interval * 2, self.POLL_MAX)


class DbEventBus:
    """Cross-process fan-out through the database (see module docstring)."""

    RETENTION_SECONDS = 600.0
    PRUNE_EVERY = 200

    def __init__(self) -> None:
        self._published = 0
        self._lock = threading.Lock()

    def subscribe(self, case_id: str) -> DbSubscription:
        from sqlalchemy import func, select

        from .. import db

        t = db.event_stream
        with db.engine.connect() as conn:
            start = conn.execute(select(func.max(t.c.id)).where(t.c.case_id == case_id)).scalar()
        return DbSubscription(case_id, start or 0)

    def unsubscribe(self, case_id: str, sub: DbSubscription) -> None:
        return None  # nothing to release: a subscription holds no connection between polls

    def publish(self, case_id: str, event: dict) -> None:
        from sqlalchemy import delete

        from .. import db

        t = db.event_stream
        now = time.time()
        with db.engine.begin() as conn:
            conn.execute(t.insert().values(case_id=case_id, payload=json.dumps(event, default=str), created_at=now))
        with self._lock:
            self._published += 1
            due = self._published % self.PRUNE_EVERY == 0
        if due:
            with db.engine.begin() as conn:
                conn.execute(delete(t).where(t.c.created_at < now - self.RETENTION_SECONDS))


def _make_bus():
    mode = os.environ.get("DIGINYAYA_EVENT_BUS", "db").strip().lower()
    return EventBus() if mode == "memory" else DbEventBus()


bus = _make_bus()


def stream_from_queue(q: "queue.Queue", *, idle_timeout: float = 120.0) -> Iterator[dict]:
    """Yield events from a subscriber queue until a terminal event or timeout."""
    last = time.time()
    while True:
        try:
            ev = q.get(timeout=1.0)
        except queue.Empty:
            if time.time() - last > idle_timeout:
                return
            # heartbeat keeps proxies from closing the connection
            yield {"type": "heartbeat", "ts": time.time()}
            continue
        last = time.time()
        yield ev
        if ev.get("type") in TERMINAL:
            return
