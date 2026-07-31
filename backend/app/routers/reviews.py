"""Human-review endpoints for cases awaiting a human decision.

Three distinct paths land a case in the review queue:
  1. app.core.safety_gate auto-escalated it (status == "escalated").
  2. A party manually requested review (POST /api/cases/{id}/request-review) --
     e.g. a Tier 1 consumer dispute, which otherwise never gets human eyes on
     it at all, since Tier 1 is meant to resolve fully autonomously.
  3. The AI drafted a Tier 2 resolution that requires human counter-signature
     (resolution.requires_human_signoff) before it's actually final.

Reviewer identity (current_reviewer, app.auth.deps) is the ONLY gate here --
deliberately NOT tied to case ownership (ensure_owner/_load_owned elsewhere in
this codebase), since a reviewer must see every case that needs review, not
just their own.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth.deps import current_reviewer
from ..auth.orm_models import User
from ..models import ReviewDecisionIn, ReviewQueueItemOut

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _reasons(case: dict) -> list[str]:
    reasons = []
    if case.get("status") == "escalated":
        reasons.append("safety-gate escalation")
    if case.get("human_review_requested"):
        reasons.append("review requested by a party")
    resolution = case.get("resolution")
    if case.get("status") == "resolved" and resolution and resolution.get("requires_human_signoff"):
        reasons.append("Tier 2 -- awaiting counter-signature")
    return reasons


def _needs_review(case: dict) -> bool:
    return bool(_reasons(case)) and not case.get("reviewer_decision")


@router.get("/queue", response_model=list[ReviewQueueItemOut])
def review_queue(user: User = Depends(current_reviewer)):
    cases = [c for c in db.all_cases() if _needs_review(c)]
    cases.sort(key=lambda c: c.get("created_at") or "")
    return [
        ReviewQueueItemOut(
            case_id=c["case_id"],
            dispute_type=c.get("dispute_type", ""),
            claimant=c.get("claimant", {}).get("name"),
            respondent=c.get("respondent", {}).get("name"),
            claim_amount=c.get("claim_amount", 0.0),
            status=c.get("status", ""),
            reason=", ".join(_reasons(c)),
            created_at=c.get("created_at"),
        )
        for c in cases
    ]


@router.get("/{case_id}")
def review_detail(case_id: str, user: User = Depends(current_reviewer)):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/{case_id}/decision")
def submit_decision(case_id: str, decision: ReviewDecisionIn, user: User = Depends(current_reviewer)):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.get("reviewer_decision"):
        raise HTTPException(status_code=409, detail="This case has already been reviewed")
    if not _needs_review(case):
        raise HTTPException(status_code=409, detail="This case is not awaiting review")

    reviewer_decision = {
        "reviewer_id": user.id,
        "reviewer_name": user.full_name,
        "approved": decision.approve,
        "note": decision.note,
        "relief_amount": decision.relief_amount,
        "decided_at": datetime.utcnow().isoformat(),
    }
    updated = db.update_case(case_id, reviewer_decision=reviewer_decision, status="resolved")
    return updated
