"""DigiNyaya FastAPI application.

Architecture:
  • Auth: real email/phone login (app/auth); cases are owned by their filer's
    user.id (IDOR-safe). The old Aadhaar-demo HMAC token scheme is retired.
  • Work runs in background jobs (jobs.py), decoupled from the HTTP connection.
  • Clients trigger a phase (POST) then subscribe to a durable + live event
    stream (GET /events?after=cursor) that replays on refresh and resumes.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta

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
from sqlalchemy.orm import Session

from . import db, jobs, llm
from .auth.db import get_db, init_auth_db
from .auth.deps import current_user
from .auth.orm_models import User
from .auth.rate_limit import enforce_call_limit
from .auth.router import me_router as auth_me_router, router as auth_router
from .core.events import TERMINAL, bus, stream_from_queue
from .core.logging import configure_app_logging
from .core.versioning import ApiVersionRewriteMiddleware
from .routers.documents import router as documents_router
from .routers.reviews import router as reviews_router
from .data.loader import DISPUTE_TYPES, get_dispute_type, load_precedents
from .language.config import (
    SUPPORTED_LANGUAGES,
    config as language_config,
    is_pipeline_language,
    is_supported_language,
    normalize_language_code,
)
from .language.gateway import UnsupportedLanguageError, get_language_gateway
from .language.logging import configure_language_logging
from .models import (
    CaseSummaryOut,
    ClaimSubmission,
    LanguageOption,
    MediationDecision,
    RespondentSubmission,
    SupportedLanguagesResponse,
)
from .security import ensure_owner, sanitize_text

# Attach the structured (or plain) handler to the "diginyaya.language" logger
# tree before any language-gateway module logs anything.
configure_language_logging(language_config)
configure_app_logging()

app = FastAPI(title="DigiNyaya API", version="0.2.0")

# The frontend is a separate static-site deploy on its own onrender.com
# domain, so this is genuinely cross-origin in production -- CORS must name
# that exact origin (credentials mode forbids "*") for cookies + auth headers
# to be accepted.
_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"]
_frontend_origin = os.getenv("DIGINYAYA_FRONTEND_URL")
if _frontend_origin and _frontend_origin not in _cors_origins:
    _cors_origins.append(_frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# See app.core.versioning's docstring: makes every /api/... route also
# reachable at /api/v1/... with zero duplicate route declarations.
app.add_middleware(ApiVersionRewriteMiddleware)

app.include_router(auth_router)
app.include_router(auth_me_router)
app.include_router(documents_router)
app.include_router(reviews_router)


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


def _resolve_target_language(case: dict, lang: str | None) -> str:
    """Pick the language to localize a response into: an explicit ``lang``
    override from the request (the frontend's current UI language selection)
    when it's a recognized code, otherwise the case's stored
    ``source_language`` -- today's behavior, unchanged for callers that don't
    pass an override.
    """
    if lang:
        normalized = normalize_language_code(lang)
        if is_supported_language(normalized) or is_pipeline_language(normalized):
            return normalized
    return case.get("source_language", language_config.pipeline_language)


def _localize_case_response(case: dict, lang: str | None = None) -> dict:
    """Build the outgoing response for a case: strip internal ``_``-prefixed
    keys (unchanged from before), then localize human-readable fields back to
    ``lang`` if given and recognized, else the case's stored
    ``source_language`` -- without mutating ``case`` itself, which stays the
    English canonical record in the DB.

    No-op passthrough when the gateway is disabled or the resolved target
    language already *is* the pipeline language (nothing to translate back),
    so this has zero behavior change for existing/English-only cases.
    """
    response = {k: v for k, v in case.items() if not k.startswith("_")}

    gw = get_language_gateway()
    source_language = _resolve_target_language(case, lang)
    if not gw.enabled or is_pipeline_language(source_language):
        return response

    # The claimant's/respondent's own original text is only usable verbatim
    # when displaying in the case's actual filing language; an explicit
    # override to a *different* language still needs a real translation of
    # the English pipeline copy below.
    viewing_in_filing_language = source_language == case.get("source_language")

    try:
        if viewing_in_filing_language and case.get("original_description"):
            response["description"] = case["original_description"]

        respondent_submission = response.get("respondent_submission")
        if (
            viewing_in_filing_language
            and isinstance(respondent_submission, dict)
            and respondent_submission.get("original_statement")
        ):
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
    init_auth_db()
    threading.Thread(target=llm.prewarm, daemon=True).start()


# ----------------------------- public ----------------------------- #
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "DigiNyaya", "cases": db.case_count()}


@app.get("/api/ai-status")
def ai_status():
    return llm.status()


@app.get("/api/dispute-types")
def dispute_types():
    return DISPUTE_TYPES


# LLM-cost-bearing endpoints -- see auth.rate_limit.enforce_call_limit's
# docstring. Limits are generous relative to real usage; they bound an
# unbounded/scripted client, not ordinary use.
_CREATE_CASE_LIMIT, _CREATE_CASE_WINDOW = 20, timedelta(hours=1)


@app.get("/api/precedents")
def precedents():
    return load_precedents()


@app.get("/api/languages", response_model=SupportedLanguagesResponse)
def languages():
    # Public (no Depends(current_user)), same as /api/dispute-types and
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


@app.get("/api/cases", response_model=list[CaseSummaryOut])
def list_my_cases(user: User = Depends(current_user)):
    """Cases the current user filed (owner_id == user.id) -- filed-by-me
    only, newest first. See CaseSummaryOut's docstring for why this can't
    yet also list cases filed against the user."""
    return [
        CaseSummaryOut(
            case_id=c["case_id"],
            dispute_type=c.get("dispute_type", ""),
            respondent=(c.get("respondent") or {}).get("name"),
            claim_amount=c.get("claim_amount", 0.0),
            status=c.get("status", ""),
            tier=c.get("tier", 1),
            tier_label=c.get("tier_label", ""),
            created_at=c.get("created_at"),
        )
        for c in db.list_cases_by_owner(user.id)
    ]


@app.post("/api/cases")
def create_case(submission: ClaimSubmission, user: User = Depends(current_user), auth_db: Session = Depends(get_db)):
    enforce_call_limit(auth_db, user.id, "create_case", limit=_CREATE_CASE_LIMIT, window=_CREATE_CASE_WINDOW)
    case_id = "DN-" + datetime.utcnow().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    tier, tier_label = _tier_for(submission.dispute_type.value)
    gw = get_language_gateway()
    sanitized_description = sanitize_text(submission.description)
    inbound = gw.to_pipeline_language(sanitized_description, declared_language=submission.language)
    case = {
        "case_id": case_id,
        "owner_id": user.id,
        # Not "awaiting_response" yet -- creating the case is no longer the
        # same moment as notifying the respondent. The claimant can attach
        # evidence and get a preliminary review first; POST .../submit is
        # the real "file & notify" action (see that endpoint below).
        "status": "draft",
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
    }


def _load_owned(case_id: str, owner_id: str) -> dict:
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    ensure_owner(case, owner_id)
    return case


@app.post("/api/cases/{case_id}/submit")
def submit_case(case_id: str, user: User = Depends(current_user)):
    """The real "file & notify respondent" action -- separate from case
    creation so a claimant can attach evidence and get a preliminary review
    first (see /preliminary-review) without the 72-hour response window
    starting, or the respondent being notified, before they're ready.
    """
    case = _load_owned(case_id, user.id)
    if case.get("status") != "draft":
        raise HTTPException(status_code=409, detail="Case has already been filed")
    db.update_case(case_id, status="awaiting_response")
    return {"case_id": case_id, "status": "awaiting_response", "respondent_deadline_hours": 72}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, lang: str | None = None, user: User = Depends(current_user)):
    case = _load_owned(case_id, user.id)
    return _localize_case_response(case, lang)


@app.post("/api/cases/{case_id}/respond")
def respond(case_id: str, submission: RespondentSubmission, user: User = Depends(current_user)):
    case = _load_owned(case_id, user.id)
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
def skip_response(case_id: str, user: User = Depends(current_user)):
    _load_owned(case_id, user.id)
    db.update_case(case_id, respondent_submission=None, status="ready")
    return {"status": "ready", "case_id": case_id, "uncontested": True}


@app.post("/api/cases/{case_id}/request-review")
def request_review(case_id: str, user: User = Depends(current_user)):
    """Manual human-review escalation -- distinct from app.core.safety_gate's
    AUTOMATIC escalation. Tier 1 (consumer_dispute) cases are otherwise never
    seen by a human at all: they resolve fully autonomously, with no
    countersignature step the way Tier 2 has. This gives either party a way
    to say "I want a human to look at this" regardless of tier or how
    confident the AI was, rather than only ever getting human eyes on a case
    when the AI itself decides to escalate.
    """
    case = _load_owned(case_id, user.id)
    if case.get("status") == "draft":
        raise HTTPException(status_code=409, detail="File the case before requesting a review")
    if case.get("human_review_requested"):
        raise HTTPException(status_code=409, detail="A human review has already been requested for this case")
    db.update_case(case_id, human_review_requested=True, human_review_requested_at=datetime.utcnow().isoformat())
    return {"case_id": case_id, "human_review_requested": True}


@app.post("/api/cases/{case_id}/run")
def run_pipeline(case_id: str, user: User = Depends(current_user)):
    case = _load_owned(case_id, user.id)
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
def mediation_decision(case_id: str, decision: MediationDecision, user: User = Depends(current_user)):
    case = _load_owned(case_id, user.id)
    if "_ctx" not in case:
        raise HTTPException(status_code=409, detail="Run the resolution pipeline before deciding mediation")
    if case.get("status") == "resolved":
        return {"status": "resolved", "accepted": case.get("mediation_accepted"), "started": False}
    if case.get("status") == "escalated":
        return {"status": "escalated", "accepted": case.get("mediation_accepted"), "started": False}
    db.update_case(case_id, status="mediation_accepted" if decision.accept else "processing")
    started = jobs.start_resolution(case_id, decision.accept)
    return {"status": "ok", "accepted": decision.accept, "started": started}


@app.get("/api/cases/{case_id}/events")
def events(case_id: str, after: int = 0, lang: str | None = None, user: User = Depends(current_user)):
    case = _load_owned(case_id, user.id)
    gw = get_language_gateway()
    source_language = _resolve_target_language(case, lang)
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