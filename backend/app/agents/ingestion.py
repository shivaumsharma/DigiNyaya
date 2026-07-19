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
    "loan_default": "Non-repayment of a loan / money lent",
    "unpaid_dues": "Unpaid dues / outstanding payment",
    "cheque_dishonour": "Cheque dishonoured (bounced)",
    "breach_of_agreement": "Breach of a written agreement",
}

# Categories allowed to be resolved fully autonomously (Tier 1) once confidence
# clears the bar below. Money recovery, contract breach and cheque bounce
# launch Tier-2-only (always AI-assisted, human sign-off required) — cheque
# bounce in particular can carry criminal exposure under Section 138 of the
# Negotiable Instruments Act, so these stay human-reviewed until precedent
# coverage and eval results justify promoting a category into this set.
TIER1_ELIGIBLE_TYPES = {"consumer_dispute"}


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
    relief_type_requested = nlp.detect_relief_type(combined)

    subtype_key = next((s for s in signals if s in SUBTYPE_LABELS), None)
    subtype = SUBTYPE_LABELS.get(subtype_key) or f"General {nlp.dispute_label(ctx.dispute_type)}"
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
        ctx.dispute_type in TIER1_ELIGIBLE_TYPES
        and ctx.claim_amount > 0
        and len(evidence) >= 1
        and subtype_key is not None
        # A non-monetary ask (injunction/declaration/replacement/possession)
        # carries different, often larger real-world consequences than a
        # refund -- ordering someone to vacate a property or undo an action
        # is not something to auto-grant the same way a routine monetary
        # refund is. Stays Tier 2 regardless of confidence.
        and relief_type_requested == "monetary"
    )
    recommended_tier = 1 if (eligible and confidence >= 0.6) else 2

    reason = (
        f"Document-based {nlp.dispute_label(ctx.dispute_type)} with a quantified monetary claim, "
        "supporting evidence and a clearly classified grievance — qualifies for fully autonomous "
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
        relief_type_requested=relief_type_requested,
    )

    detail = (
        f"Parsed {len(evidence)} evidence item(s) and the claim narrative. "
        f"Classified as '{subtype}' with {int(confidence * 100)}% confidence. "
        f"Detected {len(signals)} legal signal(s)."
        + (f" Relief sought: {relief_type_requested}." if relief_type_requested != "monetary" else "")
        + f" Recommends Tier {recommended_tier}."
    )
    return AgentResult(output=result, detail=detail, confidence=confidence, engine="scripted")
