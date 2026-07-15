"""DigiNyaya FastAPI application.

Architecture:
  • Auth: bearer tokens; cases are owned by their filer (IDOR-safe).
  • Work runs in background jobs (jobs.py), decoupled from the HTTP connection.
  • Clients trigger a phase (POST) then subscribe to a durable + live event
    stream (GET /events?after=cursor) that replays on refresh and resumes.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime

# Load backend/.env before importing modules that read config at import time
# (e.g. the llm client and security secret). Shell env vars take precedence.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import db, jobs, llm
from .core.events import TERMINAL, bus, stream_from_queue
from .data.loader import DISPUTE_TYPES, get_dispute_type, load_precedents
from .language.config import SUPPORTED_LANGUAGES, config as language_config, is_pipeline_language
from .language.gateway import UnsupportedLanguageError, get_language_gateway
from .language.logging import configure_language_logging
from .models import (
    ClaimSubmission,
    LanguageOption,
    LoginRequest,
    MediationDecision,
    RespondentSubmission,
    SupportedLanguagesResponse,
)
from .security import current_citizen, ensure_owner, make_token, sanitize_text

# Attach the structured (or plain) handler to the "diginyaya.language" logger
# tree before any language-gateway module logs anything.
configure_language_logging(language_config)

app = FastAPI(title="DigiNyaya API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SAMPLE_CLAIM = {
    "claimant_name": "Ananya Sharma",
    "respondent_name": "QuickShop Online Pvt. Ltd.",
    "dispute_type": "consumer_dispute",
    "claim_amount": 42999,
    "description": (
        "I ordered a laptop worth Rs. 42,999 from QuickShop Online on 12/03/2024. "
        "The amount was debited from my account immediately. The order was never "
        "delivered despite the app showing 'delivered' on 20/03/2024. I have raised "
        "the issue with their customer care five times but received no response. "
        "I am seeking a full refund of the amount paid online for goods I never received."
    ),
    "evidence": [
        {"filename": "order_invoice.pdf", "kind": "invoice", "note": "Tax invoice for Rs. 42,999"},
        {"filename": "bank_debit_statement.pdf", "kind": "receipt", "note": "Bank debit confirmation"},
        {"filename": "chat_with_support.png", "kind": "screenshot", "note": "5 unanswered support tickets"},
    ],
}

SAMPLE_RESPONSE = {
    "statement": (
        "The order was handed to our logistics partner. We are unable to confirm "
        "delivery and our courier has not responded. We can offer store credit."
    ),
    "accepts_liability": False,
    "counter_offer": 20000,
}


def _mask_aadhaar(last4: str) -> str:
    return f"XXXX-XXXX-{last4}"


# ----------------------------- Language Gateway helpers ----------------------------- #
# These run only at the API serving boundary: they read the English record
# already persisted by jobs.py/db.py and localize a *copy* of it for the
# outgoing response. Nothing here ever writes back to the DB, and jobs.py
# itself is never touched -- see module docstring in app/language/gateway.py
# for why translation must not happen before persistence.

# Human-readable text fields that show up, verbatim, in a mediation proposal
# or resolution document (see CaseContext.mediation / .resolution in
# core/context.py). Numeric/structural fields (amounts, dates, ids, engine
# names, cited_precedents, composite_confidence, etc.) are deliberately left
# alone -- translating those would be either meaningless or actively wrong.
_LOCALIZABLE_SCALAR_FIELDS = ("headline", "explanation", "basis", "header", "subheader", "footer")
_LOCALIZABLE_LIST_FIELDS = ("rationale", "findings", "order")


def _localize_list(items: list, target_language: str, gw) -> list:
    """Localize a list of strings via one batched gateway call, preserving
    order and any falsy/non-string entries untouched.
    """
    if not items:
        return items
    keyed = {str(i): text for i, text in enumerate(items) if isinstance(text, str) and text}
    if not keyed:
        return items
    results = gw.localize_many(keyed, target_language)
    out = list(items)
    for key, result in results.items():
        out[int(key)] = result.localized_text
    return out


def _localize_payload_dict(payload: dict, target_language: str, gw) -> dict:
    """Localize any recognized human-readable fields inside a mediation
    proposal / resolution doc shaped dict, whether it's the case-level
    ``case["mediation"]``/``case["resolution"]`` or an SSE event's
    ``payload``. Unrecognized keys pass through unchanged, so this is safe
    to call on any dict without knowing its exact shape in advance.
    """
    localized = dict(payload)
    scalars = {f: payload[f] for f in _LOCALIZABLE_SCALAR_FIELDS if isinstance(payload.get(f), str) and payload.get(f)}
    if scalars:
        results = gw.localize_many(scalars, target_language)
        for field, result in results.items():
            localized[field] = result.localized_text
    for field in _LOCALIZABLE_LIST_FIELDS:
        items = payload.get(field)
        if isinstance(items, list) and items:
            localized[field] = _localize_list(items, target_language, gw)
    return localized


def _localize_case_response(case: dict) -> dict:
    """Build the outgoing response for a case: strip internal ``_``-prefixed
    keys (unchanged from before), then localize human-readable fields back
    to the case's stored ``source_language`` -- without mutating ``case``
    itself, which stays the English canonical record in the DB.

    No-op passthrough when the gateway is disabled or the case's source
    language already *is* the pipeline language (nothing to translate back),
    so this has zero behavior change for existing/English-only cases.
    """
    response = {k: v for k, v in case.items() if not k.startswith("_")}

    gw = get_language_gateway()
    source_language = case.get("source_language", language_config.pipeline_language)
    if not gw.enabled or is_pipeline_language(source_language):
        return response

    try:
        # The claimant's/respondent's original text is already in their own
        # language -- prefer it over re-translating the English pipeline
        # copy, which would be lossier and cost an extra Sarvam call for no
        # benefit.
        if case.get("original_description"):
            response["description"] = case["original_description"]

        respondent_submission = response.get("respondent_submission")
        if isinstance(respondent_submission, dict) and respondent_submission.get("original_statement"):
            respondent_submission = dict(respondent_submission)
            respondent_submission["statement"] = respondent_submission["original_statement"]
            response["respondent_submission"] = respondent_submission

        mediation = response.get("mediation")
        if isinstance(mediation, dict):
            response["mediation"] = _localize_payload_dict(mediation, source_language, gw)

        resolution = response.get("resolution")
        if isinstance(resolution, dict):
            response["resolution"] = _localize_payload_dict(resolution, source_language, gw)
    except UnsupportedLanguageError:
        # Stored source_language on an old/bad case doesn't normalize to
        # anything the gateway recognizes -- serve the English response
        # rather than 500ing on a display-only concern.
        return {k: v for k, v in case.items() if not k.startswith("_")}

    return response


def _localize_event(event: dict, source_language: str, gw) -> dict:
    """Localize one SSE event's human-readable text before it is yielded to
    the client. Mirrors ``_localize_case_response`` but for a single event
    dict (``detail`` plus any recognized ``payload`` fields).
    """
    localized = dict(event)
    detail = event.get("detail")
    if isinstance(detail, str) and detail:
        try:
            localized["detail"] = gw.to_user_language(detail, source_language).localized_text
        except UnsupportedLanguageError:
            pass
    payload = event.get("payload")
    if isinstance(payload, dict) and payload:
        try:
            localized["payload"] = _localize_payload_dict(payload, source_language, gw)
        except UnsupportedLanguageError:
            pass
    return localized


@app.on_event("startup")
def _startup():
    db.init_db()
    threading.Thread(target=llm.prewarm, daemon=True).start()


# ----------------------------- public ----------------------------- #
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "DigiNyaya", "cases": db.case_count()}


@app.get("/api/ai-status")
def ai_status():
    return llm.status()


@app.post("/api/login")
def login(req: LoginRequest):
    if not req.aadhaar_last4.isdigit():
        raise HTTPException(status_code=400, detail="Aadhaar last 4 digits must be numeric")
    citizen_id = "citizen_" + uuid.uuid4().hex[:10]
    return {
        "citizen_id": citizen_id,
        "name": sanitize_text(req.name, max_len=120) or "Citizen",
        "aadhaar_verified": True,
        "masked_aadhaar": _mask_aadhaar(req.aadhaar_last4),
        "token": make_token(citizen_id),
    }


@app.get("/api/dispute-types")
def dispute_types():
    return DISPUTE_TYPES


@app.get("/api/precedents")
def precedents():
    return load_precedents()


@app.get("/api/languages", response_model=SupportedLanguagesResponse)
def languages():
    # Public (no Depends(current_citizen)), same as /api/dispute-types and
    # /api/precedents: static reference data, not user-specific, and needed
    # pre-login so the login screen itself can render in the user's language.
    return SupportedLanguagesResponse(
        languages=[LanguageOption(**lang) for lang in SUPPORTED_LANGUAGES],
        default=language_config.default_user_language,
        pipeline=language_config.pipeline_language,
    )


@app.get("/api/sample-claim")
def sample_claim():
    return {"claim": SAMPLE_CLAIM, "response": SAMPLE_RESPONSE}


# ----------------------------- cases (authed) ----------------------------- #
def _tier_for(dispute_type: str) -> tuple[int, str]:
    dt = get_dispute_type(dispute_type)
    if dt:
        return dt["tier"], dt["tier_label"]
    return 1, "Tier 1 — Fully Autonomous AI Resolution"


@app.post("/api/cases")
def create_case(submission: ClaimSubmission, citizen_id: str = Depends(current_citizen)):
    case_id = "DN-" + datetime.utcnow().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    tier, tier_label = _tier_for(submission.dispute_type.value)
    gw = get_language_gateway()
    sanitized_description = sanitize_text(submission.description)
    inbound = gw.to_pipeline_language(sanitized_description, declared_language=submission.language)
    case = {
        "case_id": case_id,
        "owner_id": citizen_id,
        "status": "awaiting_response",
        "tier": tier,
        "tier_label": tier_label,
        "dispute_type": submission.dispute_type.value,
        "claimant": {"name": sanitize_text(submission.claimant_name, max_len=120), "role": "claimant", "aadhaar_verified": True},
        "respondent": {"name": sanitize_text(submission.respondent_name, max_len=120), "role": "respondent", "aadhaar_verified": False},
        "claim_amount": submission.claim_amount,
        "description": inbound.pipeline_text,
        "original_description": inbound.original_text,
        "source_language": inbound.source_language,
        "evidence": [e.model_dump() for e in submission.evidence],
        "respondent_submission": None,
        "mediation": None,
        "resolution": None,
        "created_at": datetime.utcnow().isoformat(),
    }
    db.save_case(case)
    return {
        "case_id": case_id,
        "status": case["status"],
        "tier": tier,
        "tier_label": tier_label,
        "created_at": case["created_at"],
        "respondent_deadline_hours": 72,
    }


def _load_owned(case_id: str, citizen_id: str) -> dict:
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    ensure_owner(case, citizen_id)
    return case


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    return _localize_case_response(case)


@app.post("/api/cases/{case_id}/respond")
def respond(case_id: str, submission: RespondentSubmission, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    gw = get_language_gateway()
    sanitized_statement = sanitize_text(submission.statement)
    inbound = gw.to_pipeline_language(sanitized_statement, declared_language=submission.language)
    payload = submission.model_dump()
    payload["statement"] = inbound.pipeline_text
    payload["original_statement"] = inbound.original_text
    payload["language"] = inbound.source_language
    case["respondent"]["aadhaar_verified"] = True
    db.update_case(case_id, respondent_submission=payload, status="ready", respondent=case["respondent"])
    return {"status": "ready", "case_id": case_id}


@app.post("/api/cases/{case_id}/skip-response")
def skip_response(case_id: str, citizen_id: str = Depends(current_citizen)):
    _load_owned(case_id, citizen_id)
    db.update_case(case_id, respondent_submission=None, status="ready")
    return {"status": "ready", "case_id": case_id, "uncontested": True}


@app.post("/api/cases/{case_id}/run")
def run_pipeline(case_id: str, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    # Only launch the pipeline for a case that hasn't started it yet, so a page
    # refresh (which re-issues this call) never re-runs the agents.
    # "error" is included so a case that crashed mid-pipeline (e.g. a
    # transient Ollama/embeddings failure) can be retried from the same
    # endpoint, instead of being permanently stuck once jobs.py marks it
    # "error" (see jobs.py's exception handlers).
    if case.get("status") not in ("awaiting_response", "ready", "error"):
        return {"started": False, "running": jobs.is_running(case_id), "status": case.get("status")}
    started = jobs.start_pipeline(case_id)
    return {"started": started, "running": jobs.is_running(case_id)}


@app.post("/api/cases/{case_id}/mediation")
def mediation_decision(case_id: str, decision: MediationDecision, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    if "_ctx" not in case:
        raise HTTPException(status_code=409, detail="Run the resolution pipeline before deciding mediation")
    if case.get("status") == "resolved":
        return {"status": "resolved", "accepted": case.get("mediation_accepted"), "started": False}
    db.update_case(case_id, status="mediation_accepted" if decision.accept else "processing")
    started = jobs.start_resolution(case_id, decision.accept)
    return {"status": "ok", "accepted": decision.accept, "started": started}


@app.get("/api/cases/{case_id}/events")
def events(case_id: str, after: int = 0, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    gw = get_language_gateway()
    source_language = case.get("source_language", language_config.pipeline_language)
    should_localize = gw.enabled and not is_pipeline_language(source_language)

    def sse(ev: dict) -> str:
        if should_localize:
            ev = _localize_event(ev, source_language, gw)
        return f"data: {json.dumps(ev)}\n\n"

    def gen():
        q = bus.subscribe(case_id)
        try:
            delivered = after
            for ev in db.get_events(case_id, after):
                delivered = ev["seq"]
                yield sse(ev)
            if not jobs.is_running(case_id):
                # Nothing live; the client now has everything up to the current pause/terminal.
                yield "event: end\ndata: {}\n\n"
                return
            for ev in stream_from_queue(q):
                t = ev.get("type")
                if t == "heartbeat":
                    yield ": hb\n\n"
                    continue
                seq = ev.get("seq")
                if seq is not None and seq <= delivered:
                    continue  # already replayed from the durable log
                if seq is not None:
                    delivered = seq
                yield sse(ev)
                if t in TERMINAL:
                    break
            yield "event: end\ndata: {}\n\n"
        finally:
            bus.unsubscribe(case_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )