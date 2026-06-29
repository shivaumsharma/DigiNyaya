"""Events + a live pub/sub bus.

Two channels work together:
  - The append-only event LOG (SQLite, in db.py) is the durable audit trail and
    powers replay-on-refresh.
  - The in-memory BUS streams events (including per-token deltas) live to any
    currently-connected SSE clients.

A job publishes coarse events to BOTH (so they survive restart and replay) and
high-frequency token events to the bus ONLY (not worth persisting).
"""

from __future__ import annotations

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


bus = EventBus()


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
