"""Events + a live pub/sub bus.

Two channels work together:
  - The append-only event LOG (the `events` table, in db.py) is the durable audit trail and
    powers replay-on-refresh.
  - The BUS streams events (including per-token deltas) live to any currently-connected SSE clients.

A job publishes coarse events to BOTH (so they survive restart and replay) and
high-frequency token events to the bus ONLY (not worth persisting).

Two bus implementations, chosen by DIGINYAYA_EVENT_BUS:
  db      (default) events go through the `event_stream` table, so a worker PROCESS can publish and any number of API
          processes can stream them. One dispatcher thread per process polls the table (one query per tick) and feeds
          the local subscribers.
  memory  in-process only; correct only when the publisher and the SSE server are the same process (single-process dev).
Both expose subscribe / unsubscribe / publish, and subscribe returns a plain queue.Queue, so stream_from_queue and the async
astream work with either. A Redis or Postgres LISTEN/NOTIFY bus can be added behind the same three methods.
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


class DbEventBus:
    """Cross-process fan-out through the database (see module docstring).

    ONE dispatcher thread per process polls `event_stream` for new rows (a single query per tick, however many streams are
    open) and hands each row to the in-process subscribers of that case. Subscribers are plain thread-safe queues, so SSE
    streams never touch the database themselves. Rows are read from a little before the cursor and de-duplicated, because
    ids can become visible out of order when several writers commit concurrently."""

    RETENTION_SECONDS = 600.0
    PRUNE_EVERY = 200
    POLL_MIN = 0.05
    POLL_MAX = 0.25
    LOOKBACK_IDS = 500

    def __init__(self) -> None:
        self._published = 0
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue]] = {}
        self._thread: threading.Thread | None = None
        self._cursor = 0
        self._seen: collections.deque = collections.deque(maxlen=4000)
        self._seen_set: set[int] = set()

    # -- subscribers -------------------------------------------------------------------------------------------------
    def subscribe(self, case_id: str) -> "queue.Queue":
        q: queue.Queue = queue.Queue()
        with self._lock:
            first = not self._subs
            self._subs.setdefault(case_id, []).append(q)
        if first or self._thread is None or not self._thread.is_alive():
            self._start_dispatcher()
        return q

    def unsubscribe(self, case_id: str, q: "queue.Queue") -> None:
        with self._lock:
            subs = self._subs.get(case_id)
            if subs and q in subs:
                subs.remove(q)
            if subs is not None and not subs:
                self._subs.pop(case_id, None)

    # -- publishing --------------------------------------------------------------------------------------------------
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

    # -- dispatcher --------------------------------------------------------------------------------------------------
    def _start_dispatcher(self) -> None:
        from sqlalchemy import func, select

        from .. import db

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._seen.clear()
            self._seen_set.clear()
            with db.engine.connect() as conn:
                self._cursor = conn.execute(select(func.max(db.event_stream.c.id))).scalar() or 0
                # Rows already present are history: mark the lookback window as seen so it is not dispatched.
                for (row_id,) in conn.execute(select(db.event_stream.c.id).where(db.event_stream.c.id > max(self._cursor - self.LOOKBACK_IDS, 0))):
                    self._remember(row_id)
            self._thread = threading.Thread(target=self._run, name="diginyaya-event-dispatcher", daemon=True)
            self._thread.start()

    def _remember(self, row_id: int) -> bool:
        """True if this id is new (and remember it)."""
        if row_id in self._seen_set:
            return False
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(row_id)
        self._seen_set.add(row_id)
        return True

    def poll_once(self) -> int:
        """Fetch new rows and hand them to local subscribers. Returns how many rows were dispatched."""
        from sqlalchemy import select

        from .. import db

        t = db.event_stream
        with db.engine.connect() as conn:
            rows = conn.execute(select(t.c.id, t.c.case_id, t.c.payload).where(t.c.id > max(self._cursor - self.LOOKBACK_IDS, 0))
                                .order_by(t.c.id).limit(2000)).all()
        n = 0
        for row_id, case_id, payload in rows:
            self._cursor = max(self._cursor, row_id)
            if not self._remember(row_id):
                continue
            with self._lock:
                targets = list(self._subs.get(case_id, ()))
            if not targets:
                continue
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            for q in targets:
                q.put_nowait(ev)
            n += 1
        return n

    def _run(self) -> None:
        interval = self.POLL_MIN
        while True:
            with self._lock:
                if not self._subs:
                    self._thread = None   # nobody listening: stop polling; the next subscribe restarts it
                    return
            try:
                got = self.poll_once()
            except Exception:  # database briefly unavailable: keep the dispatcher alive
                got = 0
                time.sleep(1.0)
            interval = self.POLL_MIN if got else min(interval * 2, self.POLL_MAX)
            time.sleep(interval)


async def astream(q: "queue.Queue", *, idle_timeout: float = 120.0, heartbeat_every: float = 5.0):
    """Async version of stream_from_queue: waits on the subscriber queue without holding a worker thread, so thousands of
    SSE streams cost almost nothing. Yields events and periodic heartbeats; stops at a terminal event or after idle_timeout."""
    import asyncio

    last = last_hb = time.time()
    delay = 0.02
    while True:
        try:
            ev = q.get_nowait()
        except queue.Empty:
            now = time.time()
            if now - last > idle_timeout:
                return
            if now - last_hb >= heartbeat_every:
                last_hb = now
                yield {"type": "heartbeat", "ts": now}
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.1)
            continue
        delay = 0.02
        last = last_hb = time.time()
        yield ev
        if ev.get("type") in TERMINAL:
            return


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
