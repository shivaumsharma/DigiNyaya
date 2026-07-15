"""Background job runner.

Agent work runs here, NOT inside the HTTP request — so a client disconnect or
page refresh never kills (or duplicates) the pipeline. Each event is:
  • appended to the durable SQLite log (audit trail + replay), and
  • published to the in-memory bus for any live SSE subscriber.

Jobs are idempotent per case+phase: a second trigger while one is running is a
no-op, so refreshing the page won't re-run agents or burn LLM compute twice.
"""

from __future__ import annotations

import threading
from typing import Iterator

from . import db
from .core import graph
from .core.context import CaseContext
from .core.events import EPHEMERAL, bus, make_event

_lock = threading.Lock()
_running: dict[str, str] = {}  # case_id -> phase currently running


def is_running(case_id: str) -> str | None:
    with _lock:
        return _running.get(case_id)


def _claim(case_id: str, phase: str) -> bool:
    with _lock:
        if case_id in _running:
            return False
        _running[case_id] = phase
        return True


def _release(case_id: str) -> None:
    with _lock:
        _running.pop(case_id, None)


def _pump(case_id: str, events: Iterator[dict], *, on_event) -> None:
    for ev in events:
        if ev.get("type") not in EPHEMERAL:
            seq = db.append_event(case_id, ev)
            ev["seq"] = seq
        bus.publish(case_id, ev)
        on_event(ev)


def _run_pipeline(case_id: str) -> None:
    try:
        case = db.get_case(case_id)
        if case is None:
            return
        ctx = CaseContext.from_case(case)
        db.update_case(case_id, status="processing")

        def on_event(ev: dict) -> None:
            if ev.get("type") == "awaiting_decision":
                db.update_case(
                    case_id,
                    status="mediation_proposed",
                    tier=ctx.tier,
                    tier_label=ctx.tier_label,
                    mediation=ctx.mediation.model_dump() if ctx.mediation else None,
                    _ctx=ctx.model_dump(mode="json"),
                )

        _pump(case_id, graph.run_pipeline(ctx), on_event=on_event)
    except Exception as exc:  # surface failures as an event instead of dying silently
        ev = make_event("error", detail=f"Pipeline error: {exc}")
        db.append_event(case_id, ev)
        bus.publish(case_id, ev)
        # Without this, case.status stays "processing" forever: the /run
        # endpoint's guard only allows (re)starting from "awaiting_response"
        # or "ready", so a crashed case could never be retried and the UI
        # would show it "running" indefinitely even though this thread has
        # already exited.
        db.update_case(case_id, status="error")
    finally:
        _release(case_id)


def _run_resolution(case_id: str, via_mediation: bool) -> None:
    try:
        case = db.get_case(case_id)
        if case is None or "_ctx" not in case:
            ev = make_event("error", detail="Cannot resolve: pipeline context missing. Run the pipeline first.")
            db.append_event(case_id, ev)
            bus.publish(case_id, ev)
            return
        ctx = CaseContext(**case["_ctx"])

        def on_event(ev: dict) -> None:
            if ev.get("type") == "resolved":
                db.update_case(
                    case_id,
                    status="resolved",
                    resolution=ctx.resolution.model_dump() if ctx.resolution else None,
                    mediation_accepted=via_mediation,
                    _ctx=ctx.model_dump(mode="json"),
                )

        _pump(case_id, graph.run_resolution(ctx, via_mediation=via_mediation), on_event=on_event)
    except Exception as exc:
        ev = make_event("error", detail=f"Resolution error: {exc}")
        db.append_event(case_id, ev)
        bus.publish(case_id, ev)
        db.update_case(case_id, status="error")
    finally:
        _release(case_id)


def start_pipeline(case_id: str) -> bool:
    if not _claim(case_id, "pipeline"):
        return False
    threading.Thread(target=_run_pipeline, args=(case_id,), daemon=True).start()
    return True


def start_resolution(case_id: str, via_mediation: bool) -> bool:
    if not _claim(case_id, "resolution"):
        return False
    threading.Thread(target=_run_resolution, args=(case_id, via_mediation), daemon=True).start()
    return True