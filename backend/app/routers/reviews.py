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

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth.deps import current_reviewer
from ..auth.orm_models import User
from ..models import ReviewDecisionIn, ReviewQueueItemOut

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

# Produced offline by scripts/judge_real_outcomes.py, which re-runs the
# pipeline on a real-judgment dataset and has an LLM judge compare each AI
# resolution against what a real Indian court actually decided in that case
# (match / partial / mismatch). NOT committed to git (backend/data_cache/ is
# gitignored -- see .gitignore) and NOT regenerated automatically, since a
# real run costs real Sarvam calls; this endpoint only ever reads whatever
# snapshot happens to exist, and degrades to "unavailable" rather than
# erroring when it doesn't (e.g. a fresh deploy that never ran the script).
_EVAL_METRICS_PATH = Path(__file__).resolve().parent.parent.parent / "data_cache" / "real_judgment_verdict_comparison.json"


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


@router.get("/ops-metrics")
def ops_metrics(user: User = Depends(current_reviewer)):
    """Case-volume operational overview: status/tier/dispute-type split and
    escalation rate. Reviewer-gated for the same reason as everything else
    in this router -- there's no general admin role in this app (see
    README's Known issues), just the one is_reviewer capability.

    Two escalation rates are reported because they answer different
    questions: of ALL cases filed so far (including ones still in flight
    that haven't had a chance to escalate yet), vs. of cases that reached a
    terminal outcome (resolved or escalated), what fraction escalated.
    The second is the more meaningful one for judging the pipeline's actual
    behaviour; the first is diluted by in-progress cases.
    """
    cases = db.all_cases()
    total = len(cases)

    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_dispute_type: dict[str, int] = {}
    escalated_count = 0
    resolved_count = 0
    awaiting_review_count = 0

    for c in cases:
        status = c.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1

        tier = str(c.get("tier", "unknown"))
        by_tier[tier] = by_tier.get(tier, 0) + 1

        dispute_type = c.get("dispute_type", "unknown")
        by_dispute_type[dispute_type] = by_dispute_type.get(dispute_type, 0) + 1

        if status == "escalated":
            escalated_count += 1
        elif status == "resolved":
            resolved_count += 1

        if _needs_review(c):
            awaiting_review_count += 1

    terminal_count = escalated_count + resolved_count

    return {
        "total_cases": total,
        "by_status": by_status,
        "by_tier": by_tier,
        "by_dispute_type": by_dispute_type,
        "escalation_rate_of_total": round(escalated_count / total, 3) if total else None,
        "escalation_rate_of_terminal": round(escalated_count / terminal_count, 3) if terminal_count else None,
        "awaiting_review_count": awaiting_review_count,
    }


@router.get("/eval-metrics")
def eval_metrics(user: User = Depends(current_reviewer)):
    """Real-judgment eval summary: how often the AI's actual resolution
    matched what a real Indian court decided in that case (see
    _EVAL_METRICS_PATH's comment for how this file is produced).

    Reviewer-only, not public: the sample is small (currently ~27 cases)
    and the comparison methodology (LLM-as-judge, free-text verdicts) is
    a calibration signal for the humans making final decisions, not a
    validated accuracy claim ready for citizens to see out of context.

    Returns {"available": False} rather than 404/500 when no eval snapshot
    exists yet in this environment (data_cache/ is gitignored and this
    file is only produced by manually running scripts/judge_real_outcomes.py).
    """
    if not _EVAL_METRICS_PATH.exists():
        return {"available": False}

    try:
        results = json.loads(_EVAL_METRICS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"available": False}

    counts = {"match": 0, "partial": 0, "mismatch": 0, "judge_failed": 0}
    for r in results:
        verdict = r.get("verdict")
        if verdict in counts:
            counts[verdict] += 1

    graded = counts["match"] + counts["partial"] + counts["mismatch"]
    generated_at = datetime.fromtimestamp(_EVAL_METRICS_PATH.stat().st_mtime, tz=timezone.utc).isoformat()

    return {
        "available": True,
        "generated_at": generated_at,
        "sample_size": len(results),
        "counts": counts,
        "match_rate": round(counts["match"] / graded, 3) if graded else None,
        "match_or_partial_rate": round((counts["match"] + counts["partial"]) / graded, 3) if graded else None,
        "methodology": (
            "Each case's AI-drafted resolution is compared by an LLM judge against the real "
            "historical court outcome for that same real-world dispute (scripts/judge_real_outcomes.py). "
            "match_rate/match_or_partial_rate exclude judge_failed cases from the denominator. "
            "Small sample -- treat as a calibration signal, not a validated accuracy figure."
        ),
    }


@router.get("/{case_id}")
def review_detail(case_id: str, user: User = Depends(current_reviewer)):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/audit-verify")
def audit_verify(case_id: str, user: User = Depends(current_reviewer)):
    """Recompute this case's event hash chain (app.db.verify_case_events)
    and report whether it's intact -- lets a reviewer confirm the audit
    trail they're about to rely on for a decision hasn't been altered.
    """
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return db.verify_case_events(case_id)


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
