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
from .models import ClaimSubmission, LoginRequest, MediationDecision, RespondentSubmission
from .security import current_citizen, ensure_owner, make_token, sanitize_text

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
        "description": sanitize_text(submission.description),
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
    return {k: v for k, v in case.items() if not k.startswith("_")}


@app.post("/api/cases/{case_id}/respond")
def respond(case_id: str, submission: RespondentSubmission, citizen_id: str = Depends(current_citizen)):
    case = _load_owned(case_id, citizen_id)
    payload = submission.model_dump()
    payload["statement"] = sanitize_text(payload.get("statement", ""))
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
    if case.get("status") not in ("awaiting_response", "ready"):
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
    _load_owned(case_id, citizen_id)

    def sse(ev: dict) -> str:
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
