"""Agent 1 — Case Ingestion Agent.

Parses the claim + evidence into structured facts and produces a *confidence*
in its classification and a *recommended tier*. The orchestrator uses these to
route the case (Tier 1 autonomous vs. escalate) — so this agent's output is no
longer cosmetic; it actually steers the workflow.
"""

from __future__ import annotations

from ..core.context import CaseContext, IngestionResult
from . import nlp
from .base import AgentResult

SUBTYPE_LABELS = {
    "non_delivery": "Non-delivery of paid goods",
    "defective_product": "Defective / faulty product",
    "counterfeit": "Counterfeit goods sold",
    "misrepresentation": "Misrepresentation / product not as described",
    "service_deficiency": "Deficiency of service",
    "wrongful_billing": "Wrongful billing / overcharge",
    "unauthorized_transaction": "Unauthorized transaction",
    "subscription": "Unwanted auto-renewal / subscription",
    "repair_delay": "Warranty repair delay",
}


def run(ctx: CaseContext) -> AgentResult:
    claim_text = ctx.description
    evidence = ctx.evidence
    respondent = ctx.respondent_submission

    combined = claim_text
    if respondent:
        combined += "\n" + respondent.get("statement", "")

    signals = nlp.extract_signals(combined)
    amounts = nlp.extract_amounts(claim_text)
    dates = nlp.extract_dates(claim_text)

    subtype_key = next((s for s in signals if s in SUBTYPE_LABELS), None)
    subtype = SUBTYPE_LABELS.get(subtype_key, "General consumer grievance")
    evidence_kinds = sorted({e.get("kind", "document") for e in evidence})

    # Confidence: driven by how clearly the case presents (recognised subtype,
    # evidence on record, a quantified claim). This is a real routing signal.
    conf = 0.4
    if subtype_key:
        conf += 0.25
    if len(evidence) >= 2:
        conf += 0.2
    elif len(evidence) == 1:
        conf += 0.1
    if ctx.claim_amount > 0:
        conf += 0.1
    if len(signals) >= 2:
        conf += 0.05
    confidence = round(min(conf, 0.98), 2)

    eligible = (
        ctx.dispute_type == "consumer_dispute"
        and ctx.claim_amount > 0
        and len(evidence) >= 1
        and subtype_key is not None
    )
    recommended_tier = 1 if (eligible and confidence >= 0.6) else 2

    reason = (
        "Document-based consumer dispute with a quantified monetary claim, supporting "
        "evidence and a clearly classified grievance — qualifies for fully autonomous "
        "Tier 1 resolution."
        if recommended_tier == 1
        else "Classification confidence or evidence is insufficient for full autonomy — "
        "recommend escalation to an AI-assisted human-reviewed tier."
    )

    result = IngestionResult(
        dispute_subtype=subtype,
        signals=signals,
        claim_amount=ctx.claim_amount,
        claim_amount_display=nlp.inr(ctx.claim_amount),
        amounts_detected=[nlp.inr(a) for a in amounts],
        key_dates=dates,
        evidence_count=len(evidence),
        evidence_kinds=evidence_kinds,
        respondent_responded=respondent is not None,
        tier1_eligible=eligible,
        recommended_tier=recommended_tier,
        confidence=confidence,
        reasoning=reason,
    )

    detail = (
        f"Parsed {len(evidence)} evidence item(s) and the claim narrative. "
        f"Classified as '{subtype}' with {int(confidence * 100)}% confidence. "
        f"Detected {len(signals)} legal signal(s). Recommends Tier {recommended_tier}."
    )
    return AgentResult(output=result, detail=detail, confidence=confidence, engine="scripted")
