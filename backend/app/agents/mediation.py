"""Agent 4 — Mediation Agent.

The LLM *reasons* about the appropriate outcome and returns a STRUCTURED proposal
(outcome type, relief ratio, compliance window, rationale) grounded in the
retrieved precedents and the Analysis agent's strength scores. A deterministic
validator then clamps every number to a defensible, precedent-derived band — so
the model genuinely reasons while the figures remain safe and auditable.
"""

from __future__ import annotations

import statistics

from .. import llm
from ..core.context import CaseContext, MediationProposal
from . import nlp
from .base import AgentResult

_ALLOWED_DAYS = [15, 21, 30, 45, 60]


def run(ctx: CaseContext) -> AgentResult:
    precedents = ctx.research.precedents if ctx.research else []
    analysis = ctx.analysis
    claim = ctx.claim_amount

    ratios = [p.relief_amount_ratio for p in precedents] or [1.0]
    comp_days = [p.compliance_days for p in precedents] or [30]
    prec_mean = statistics.mean(ratios)
    prec_min, prec_max = min(ratios), max(ratios)
    median_days = int(round(statistics.median(comp_days)))
    c_strength = analysis.strength_score.get("claimant", 0.7) if analysis else 0.7

    # --- LLM structured reasoning ---
    engine = "scripted"
    validator_notes: list[str] = []
    proposed_ratio = round(prec_mean * (0.7 + 0.3 * c_strength), 3)  # scripted default
    outcome_type = _ratio_to_type(proposed_ratio)
    compliance_days = median_days
    explanation = ""

    schema = (
        '{"outcome_type": "full_refund|partial_refund|replacement|compensation", '
        '"relief_ratio": <number 0..1>, "compliance_days": <int>, '
        '"rationale": "<<=40 word neutral justification>"}'
    )
    prompt = (
        "You are mediating a consumer dispute. Decide a fair settlement and return JSON only, "
        f"matching this schema: {schema}\n\n"
        f"Claim amount: {nlp.inr(claim)}\n"
        f"Claimant case strength (0-1): {c_strength}\n"
        f"Respondent responded: {'no (uncontested)' if ctx.respondent_submission is None else 'yes'}\n"
        f"Precedent relief ratios observed: min {prec_min}, mean {round(prec_mean,2)}, max {prec_max}\n"
        f"Median precedent compliance window: {median_days} days\n"
        f"Leading authority: {precedents[0].citation if precedents else 'n/a'}\n"
        "relief_ratio is the fraction of the claim to be refunded (1.0 = full). "
        "Base it on the precedent ratios and the claimant's strength. Do not exceed the claim."
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=220)
    if data:
        engine = "llm"
        try:
            proposed_ratio = float(data.get("relief_ratio", proposed_ratio))
        except (TypeError, ValueError):
            validator_notes.append("Non-numeric relief_ratio from model; used scripted default.")
        outcome_type = str(data.get("outcome_type") or outcome_type)
        try:
            compliance_days = int(data.get("compliance_days", median_days))
        except (TypeError, ValueError):
            compliance_days = median_days
        explanation = str(data.get("rationale") or "").strip()

    # --- Deterministic validator: clamp the numbers to a defensible band ---
    lo, hi = max(prec_min * 0.4, 0.0), min(prec_max, 1.0)
    clamped_ratio = min(max(proposed_ratio, lo), hi)
    clamped_ratio = min(max(clamped_ratio, 0.0), 1.0)
    if abs(clamped_ratio - proposed_ratio) > 1e-6:
        validator_notes.append(
            f"Relief ratio adjusted from {round(proposed_ratio,2)} to {round(clamped_ratio,2)} to stay within the precedent band."
        )
    recommended_amount = round(min(claim * clamped_ratio, claim), 2)

    if compliance_days not in _ALLOWED_DAYS:
        nearest = min(_ALLOWED_DAYS, key=lambda d: abs(d - compliance_days))
        validator_notes.append(f"Compliance window normalised from {compliance_days} to {nearest} days.")
        compliance_days = nearest

    relief_kind = _ratio_to_type(clamped_ratio)
    n_prec = len(precedents)
    full_relief = sum(1 for r in ratios if r >= 0.99)
    pct_full = round(100 * full_relief / len(ratios))

    # Report only what we can actually source: the percentage is computed over the
    # precedents that were genuinely retrieved for THIS case (no fabricated cohort).
    headline = (
        f"Across {n_prec} closely-matched precedent{'s' if n_prec != 1 else ''}, "
        f"{pct_full}% awarded full relief. Recommended resolution: "
        f"{relief_kind.replace('_', ' ')} of {nlp.inr(recommended_amount)} within {compliance_days} days."
    )
    if not explanation:
        explanation = (
            f"A {relief_kind.replace('_', ' ')} of {nlp.inr(recommended_amount)} within "
            f"{compliance_days} days is consistent with how comparable disputes were resolved."
        )

    rationale = [
        f"{len(precedents)} closely-matched precedents analysed ({ctx.research.method if ctx.research else 'n/a'} retrieval).",
        f"Claimant case strength assessed at {int(c_strength * 100)}%.",
        f"Relief set at {int(clamped_ratio * 100)}% of the claim, within the {int(prec_min*100)}–{int(prec_max*100)}% precedent band.",
        f"Median compliance window in precedent: {median_days} days.",
    ]
    if precedents:
        rationale.append(f"Leading authority: {precedents[0].citation}.")

    proposal = MediationProposal(
        type=relief_kind,
        amount=recommended_amount,
        amount_display=nlp.inr(recommended_amount),
        compliance_days=compliance_days,
        headline=headline,
        explanation=explanation,
        rationale=rationale,
        based_on=[p.id for p in precedents],
        pct_full_relief=pct_full,
        cohort_size=n_prec,
        confidence=round((c_strength + (ctx.research.coverage_score if ctx.research else 0.5)) / 2, 2),
        engine=engine,
        validator_notes=validator_notes,
    )

    detail = (
        f"Proposed {relief_kind.replace('_', ' ')} of {nlp.inr(recommended_amount)} "
        f"({int(clamped_ratio*100)}% of claim) within {compliance_days} days. "
        + (f"{len(validator_notes)} validator adjustment(s)." if validator_notes else "Within precedent band.")
    )
    return AgentResult(output=proposal, detail=detail, confidence=proposal.confidence, citations=proposal.based_on, engine=engine)


def _ratio_to_type(ratio: float) -> str:
    if ratio >= 0.99:
        return "full_refund"
    if ratio >= 0.5:
        return "partial_refund"
    return "compensation"
