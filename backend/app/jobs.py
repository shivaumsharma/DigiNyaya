"""Background job runner.

Agent work runs here, NOT inside the HTTP request — so a client disconnect or
page refresh never kills (or duplicates) the pipeline. Each event is:
  • appended to the durable SQLite log (audit trail + replay), and
  • published to the in-memory bus for any live SSE subscriber.

Jobs are idempotent per case+phase: a second trigger while one is running is a
no-op, so refreshing the page won't re-run agents or burn LLM compute twice.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

from . import db
from .core import graph
from .core.context import CaseContext
from .core.events import EPHEMERAL, bus, make_event
from .documents import extraction as doc_extraction
from .storage import get_storage

logger = logging.getLogger("diginyaya.jobs")

# Every start_*() below used to spawn a raw, unbounded threading.Thread per
# call -- fine at low volume, but nothing capped how many could pile up
# concurrently (each one making real LLM/OCR calls) on a single small
# instance. A single shared bounded pool caps TOTAL background work across
# pipeline/resolution/extraction/discrepancy runs together, since they all
# compete for the same CPU/memory/Sarvam-quota regardless of category --
# more relevant now than ever on the free-tier t3.micro EB instance (1
# vCPU). The existing per-case/per-document claim registries below (
# `_running`, `_running_docs`, `_running_discrepancy`) are unchanged and
# still do their job of preventing a duplicate run of the SAME case/doc;
# this pool only bounds how many DIFFERENT cases/docs can run at once.
# Submitted work queues (does not drop) once the pool is full, so a burst
# just waits its turn instead of failing or being unbounded.
_MAX_WORKERS = int(os.environ.get("DIGINYAYA_JOB_WORKERS", "4"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="diginyaya-job")


def _log_status(case_id: str, status: str, **extra) -> None:
    logger.info(
        "case status transition",
        extra={"event": "case_status_transition", "case_id": case_id, "status": status, **extra},
    )


_lock = threading.Lock()
_running: dict[str, str] = {}  # case_id -> phase currently running

# Separate claim registries from `_running` above: extraction is per-DOCUMENT
# (several documents in one case must be able to extract concurrently, and
# extraction shouldn't be serialized behind -- or block -- the main dispute
# pipeline/resolution phases, which touch entirely different tables).
_doc_lock = threading.Lock()
_running_docs: dict[str, str] = {}  # document_id -> "extracting"
_running_discrepancy: dict[str, bool] = {}  # case_id -> True while a check is running


def is_document_extracting(document_id: str) -> bool:
    with _doc_lock:
        return document_id in _running_docs


def _claim_doc(document_id: str) -> bool:
    with _doc_lock:
        if document_id in _running_docs:
            return False
        _running_docs[document_id] = "extracting"
        return True


def _release_doc(document_id: str) -> None:
    with _doc_lock:
        _running_docs.pop(document_id, None)


def is_discrepancy_check_running(case_id: str) -> bool:
    with _doc_lock:
        return _running_discrepancy.get(case_id, False)


def _claim_discrepancy(case_id: str) -> bool:
    with _doc_lock:
        if _running_discrepancy.get(case_id):
            return False
        _running_discrepancy[case_id] = True
        return True


def _release_discrepancy(case_id: str) -> None:
    with _doc_lock:
        _running_discrepancy.pop(case_id, None)


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
        _log_status(case_id, "processing")

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
                _log_status(case_id, "mediation_proposed", tier=ctx.tier)
            elif ev.get("type") == "escalated_terminal":
                # Safety Gate CHECKPOINT A blocked the case before any agent
                # ran -- persist the escalation, not a pipeline result.
                db.update_case(
                    case_id,
                    status="escalated",
                    escalation=ctx.escalation,
                    _ctx=ctx.model_dump(mode="json"),
                )
                _log_status(case_id, "escalated", checkpoint="A", escalation=ctx.escalation)

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
        _log_status(case_id, "error", detail=str(exc))
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
                _log_status(case_id, "resolved", via_mediation=via_mediation)
            elif ev.get("type") == "escalated_terminal":
                # Safety Gate CHECKPOINT B discarded the drafted resolution --
                # persist the escalation instead; the resolution the agents
                # produced is never written to the case record at all.
                db.update_case(
                    case_id,
                    status="escalated",
                    escalation=ctx.escalation,
                    mediation_accepted=via_mediation,
                    _ctx=ctx.model_dump(mode="json"),
                )
                _log_status(case_id, "escalated", checkpoint="B", escalation=ctx.escalation)

        _pump(case_id, graph.run_resolution(ctx, via_mediation=via_mediation), on_event=on_event)
    except Exception as exc:
        ev = make_event("error", detail=f"Resolution error: {exc}")
        db.append_event(case_id, ev)
        bus.publish(case_id, ev)
        db.update_case(case_id, status="error")
        _log_status(case_id, "error", detail=str(exc))
    finally:
        _release(case_id)


def run_extraction(document_id: str) -> Iterator[dict]:
    """Generator, same node-style as graph.py's nodes: read the stored file
    and extract its text (native or OCR), yielding progress/result events.
    Pure orchestration -- persisting the result is the caller's job
    (_run_extraction below), same pure-generator/persisting-wrapper split as
    graph.py's nodes vs. _run_pipeline."""
    doc = db.get_document(document_id)
    if doc is None:
        yield make_event("error", agent="documents", detail=f"Document {document_id} not found.")
        return

    yield make_event(
        "document_extraction", agent="documents", status="running",
        detail=f"Extracting text from {doc['original_filename']}…",
        payload={"document_id": document_id},
    )
    try:
        raw = get_storage().read(doc["storage_path"])
    except Exception as exc:
        yield make_event(
            "document_extraction_failed", agent="documents", status="error",
            detail=f"Could not read stored file for {doc['original_filename']}: {exc}",
            payload={"document_id": document_id, "error": str(exc)},
        )
        return

    case = db.get_case(doc["case_id"])
    language = (case or {}).get("source_language") or "en-IN"
    result = doc_extraction.extract_document(raw, doc["mime_type"], language=language)
    if result.error:
        yield make_event(
            "document_extraction_failed", agent="documents", status="error",
            detail=f"Extraction failed for {doc['original_filename']}: {result.error}",
            payload={"document_id": document_id, "error": result.error},
        )
        return

    yield make_event(
        "document_extracted", agent="documents", status="done",
        detail=f"Extracted {doc['original_filename']} ({result.engine}, {len(result.pages)} page(s)).",
        payload={
            "document_id": document_id,
            "is_scanned": result.is_scanned,
            "ocr_confidence": result.ocr_confidence,
            "engine": result.engine,
            "raw_text": result.raw_text,
            "cleaned_text": result.cleaned_text,
        },
    )


def _run_extraction(document_id: str) -> None:
    try:
        doc = db.get_document(document_id)
        if doc is None:
            return
        case_id = doc["case_id"]
        db.update_document(document_id, extraction_status="processing")

        def on_event(ev: dict) -> None:
            payload = ev.get("payload", {})
            if ev.get("type") == "document_extracted":
                db.update_document(
                    document_id,
                    extraction_status="complete",
                    is_scanned=payload.get("is_scanned", False),
                    ocr_confidence=payload.get("ocr_confidence"),
                    ocr_engine=payload.get("engine"),
                    raw_ocr_text=payload.get("raw_text", ""),
                    cleaned_text=payload.get("cleaned_text", ""),
                )
            elif ev.get("type") in ("document_extraction_failed", "error"):
                db.update_document(document_id, extraction_status="failed", error_message=payload.get("error", ""))

        _pump(case_id, run_extraction(document_id), on_event=on_event)

        # Auto-trigger discrepancy-check once every document in the case has
        # finished (successfully or not) and at least one succeeded -- a
        # single-document case has nothing to cross-check yet, so this is a
        # no-op until a second document lands or the endpoint is called
        # explicitly.
        docs = db.list_documents(case_id)
        finished = docs and all(d["extraction_status"] in ("complete", "failed") for d in docs)
        any_complete = any(d["extraction_status"] == "complete" for d in docs)
        if finished and any_complete and len(docs) > 1:
            start_discrepancy_check(case_id)
    except Exception as exc:
        doc = db.get_document(document_id)
        db.update_document(document_id, extraction_status="failed", error_message=str(exc))
        if doc:
            ev = make_event("error", agent="documents", detail=f"Extraction error: {exc}")
            db.append_event(doc["case_id"], ev)
            bus.publish(doc["case_id"], ev)
    finally:
        _release_doc(document_id)


def start_extraction(document_id: str) -> bool:
    if not _claim_doc(document_id):
        return False
    _executor.submit(_run_extraction, document_id)
    return True


def _run_discrepancy_check(case_id: str) -> None:
    # Local import: keeps app.jobs from importing app.agents.discrepancy at
    # module load time, mirroring how graph.py's node functions are the only
    # place that imports the agent modules -- jobs.py otherwise only knows
    # about graph.py, not individual agents.
    from .agents import discrepancy

    try:
        ev = make_event("discrepancy_check", agent="discrepancy", status="running", detail="Checking case documents for discrepancies…")
        seq = db.append_event(case_id, ev)
        ev["seq"] = seq
        bus.publish(case_id, ev)

        def on_event(_ev: dict) -> None:
            pass  # discrepancy.run_discrepancy_check persists each row itself as it's produced

        _pump(case_id, discrepancy.run_discrepancy_check(case_id), on_event=on_event)
    except Exception as exc:
        ev = make_event("error", agent="discrepancy", detail=f"Discrepancy check error: {exc}")
        db.append_event(case_id, ev)
        bus.publish(case_id, ev)
    finally:
        _release_discrepancy(case_id)


def start_discrepancy_check(case_id: str) -> bool:
    if not _claim_discrepancy(case_id):
        return False
    _executor.submit(_run_discrepancy_check, case_id)
    return True


def start_pipeline(case_id: str) -> bool:
    if not _claim(case_id, "pipeline"):
        return False
    _executor.submit(_run_pipeline, case_id)
    return True


def start_resolution(case_id: str, via_mediation: bool) -> bool:
    if not _claim(case_id, "resolution"):
        return False
    _executor.submit(_run_resolution, case_id, via_mediation)
    return True