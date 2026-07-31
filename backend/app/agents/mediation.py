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

# Simple interest rate applied to any non-dismissed award, per annum from the
# date of the order until payment. 6% is the rate most commonly seen across
# the precedent corpus's own money-decree outcomes (and is the reasonable
# default courts fall back to under Section 34 CPC absent a contractual
# rate) -- a flat default, not case-by-case computed, since we don't
# reliably have the transaction date needed to compute a precise historical
# interest amount. Real courts routinely award interest as an ONGOING RATE
# rather than a single precomputed lump sum, so expressing it as a rate +
# order clause (see resolution.py) matches how the relief is actually
# phrased in practice, rather than requiring exact date math.
_DEFAULT_INTEREST_RATE_PCT = 6.0

# Human-readable phrase for each non-monetary relief kind (app.agents.nlp's
# detect_relief_type values), used in place of the "<kind> of Rs. X" phrasing
# that only makes sense for a monetary outcome.
_NON_MONETARY_KINDS = {
    "injunction": "an injunction directing the respondent to stop/undo the conduct complained of",
    "declaration": "a declaration in the claimant's favour on the matter in dispute",
    "replacement": "replacement of the goods/services in question",
    "possession": "an order restoring possession of the property to the claimant",
    "partition": "a decree of partition declaring each party's share in the property",
    "reinstatement": "reinstatement of the claimant to their position with continuity of service",
    "arbitration_referral": (
        "an order referring the parties to arbitration in accordance with the "
        "arbitration clause, with no adjudication of the merits by this forum"
    ),
}

# No non-monetary kind gets an automatic "in addition, pay incidental
# compensation" clause by default. Originally only arbitration_referral was
# excluded (a forum that isn't deciding the merits at all obviously can't
# also award damages), on the assumption injunction/possession/declaration/
# replacement claims routinely succeed on a SEPARATE incidental-damages
# claim alongside the primary relief. Real-judgment testing at a larger
# (200-case) scale showed the opposite is the more common pattern: courts
# very often grant the injunction/possession/etc. while EXPRESSLY denying an
# accompanying damages claim for insufficient proof (e.g. IK-EVAL-103304687:
# injunction granted, "dismissed the plaintiff's claim for damages... finding
# that the plaintiff had failed to prove" them) -- awarding incidental
# compensation by default was a bigger source of real-judgment mismatches
# than never awarding it. See _relief_phrase() and the interest-rate
# computation below.
# "reinstatement" is deliberately NOT in this set: back wages are the
# standard, expected companion to a reinstatement order (real Labour Court
# judgments consistently award both together), not a separate speculative
# damages claim the way incidental compensation is for the other kinds above.
_NO_INCIDENTAL_AMOUNT_KINDS = {"arbitration_referral", "injunction", "possession", "declaration", "replacement", "partition"}


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
    r_strength = analysis.strength_score.get("respondent", 0.3) if analysis else 0.3

    # --- LLM structured reasoning ---
    engine = "scripted"
    validator_notes: list[str] = []
    # Scripted default: relief should track how much stronger the claimant's
    # case is than the respondent's, not the claimant's score in isolation.
    # The old formula (`prec_mean * (0.7 + 0.3*c_strength)`) compressed the
    # multiplier into 0.7..1.0 regardless of the respondent's position, so
    # even a claimant analysis.py rated barely-stronger-than-weak still got
    # ~70%+ of the precedent mean -- structurally never near zero. Net
    # strength lets a respondent whose position matches or beats the
    # claimant's genuinely win no relief.
    net_strength = c_strength - r_strength  # -1..+1, positive favors claimant
    proposed_ratio = round(prec_mean * min(max(net_strength, 0.0) * 1.4, 1.0), 3)
    outcome_type = _ratio_to_type(proposed_ratio)
    compliance_days = median_days
    explanation = ""

    schema = (
        '{"outcome_type": "dismissed|full_refund|partial_refund|replacement|compensation", '
        '"relief_ratio": <number 0..1>, "compliance_days": <int>, '
        '"rationale": "<<=40 word neutral justification>"}'
    )
    prompt = (
        f"You are mediating a {nlp.dispute_label(ctx.dispute_type)}. Decide a fair settlement and return JSON only, "
        f"matching this schema: {schema}\n\n"
        f"Claim amount: {nlp.inr(claim)}\n"
        f"Claimant case strength (0-1): {c_strength}\n"
        f"Respondent case strength (0-1): {r_strength}\n"
        f"Respondent responded: {'no (uncontested)' if ctx.respondent_submission is None else 'yes'}\n"
        f"Precedent relief ratios observed: min {prec_min}, mean {round(prec_mean,2)}, max {prec_max}\n"
        f"Median precedent compliance window: {median_days} days\n"
        f"Leading authority: {precedents[0].citation if precedents else 'n/a'}\n"
        "relief_ratio is the fraction of the claim to be refunded (1.0 = full, 0.0 = no relief at all). "
        "Base it on the precedent ratios and the RELATIVE strength of both sides -- if the respondent's "
        "case is as strong as or stronger than the claimant's, use outcome_type 'dismissed' and "
        "relief_ratio 0.0. Do not exceed the claim."
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=600)
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
    # The floor is 0.0, NOT a fraction of prec_min. A floor derived from
    # prec_min (e.g. `prec_min * 0.4`) forces SOME relief onto the claimant
    # whenever the retrieved precedents themselves ever won anything, which
    # is most of the time -- meaning the system could never actually agree
    # with a respondent, no matter how weak the claimant's case was. The
    # ceiling still caps relief at what precedent supports (never award more
    # than the best comparable case did).
    lo, hi = 0.0, min(prec_max, 1.0)
    clamped_ratio = min(max(proposed_ratio, lo), hi)

    # Deterministic enforcement of the SAME rule already given to the LLM
    # above ("if the respondent's case is as strong as or stronger, use
    # outcome_type 'dismissed' and relief_ratio 0.0") -- until now nothing
    # actually checked whether the model followed it. Real-judgment testing
    # found the model sometimes proposes partial relief while its own
    # rationale text acknowledges the respondent's case is stronger; the
    # precedent-band clamp above doesn't catch this since a mid-range ratio
    # is still "within band". This closes that gap regardless of which
    # engine (scripted default or LLM) produced proposed_ratio.
    if r_strength >= c_strength and clamped_ratio > 0:
        validator_notes.append(
            f"Relief ratio forced from {round(clamped_ratio, 2)} to 0.0: respondent's case strength "
            f"({int(r_strength * 100)}%) is at least as strong as the claimant's ({int(c_strength * 100)}%)."
        )
        clamped_ratio = 0.0
        # The LLM's own rationale text (if any) was written for the relief
        # ratio it originally proposed -- now overridden -- so it no longer
        # matches the actual outcome. Discard it; the scripted dismissed
        # explanation below (`if not explanation:`) takes over instead.
        explanation = ""
    elif abs(clamped_ratio - proposed_ratio) > 1e-6:
        validator_notes.append(
            f"Relief ratio adjusted from {round(proposed_ratio,2)} to {round(clamped_ratio,2)} to stay within the precedent band."
        )
    recommended_amount = round(min(claim * clamped_ratio, claim), 2)

    if compliance_days not in _ALLOWED_DAYS:
        nearest = min(_ALLOWED_DAYS, key=lambda d: abs(d - compliance_days))
        validator_notes.append(f"Compliance window normalised from {compliance_days} to {nearest} days.")
        compliance_days = nearest

    relief_kind = _ratio_to_type(clamped_ratio)
    # A non-monetary ask (injunction/declaration/replacement/possession)
    # overrides the ratio-derived monetary bucket -- UNLESS the case was
    # dismissed outright, in which case there's nothing to grant regardless
    # of what kind of relief was sought. `recommended_amount` is left as
    # computed: real claims routinely seek incidental damages ALONGSIDE a
    # non-monetary primary ask (e.g. injunction + damages), so it still
    # feeds a secondary monetary line in resolution.py's order.
    requested_relief = ctx.ingestion.relief_type_requested if ctx.ingestion else "monetary"
    if requested_relief != "monetary" and (relief_kind != "dismissed" or requested_relief == "arbitration_referral"):
        # Arbitration referral is the one non-monetary kind that overrides
        # even an outright "dismissed" ratio: unlike injunction/declaration/
        # replacement/possession (genuinely merits-based asks -- nothing to
        # grant if the claimant's case was too weak), a referral to
        # arbitration is a THRESHOLD/jurisdictional question that doesn't
        # depend on how strong either side's merits case is at all. Found
        # via a synthetic test: a weak claimant + an arbitration-clause
        # defense pushed net_strength negative, so `_ratio_to_type()`
        # returned "dismissed" before this override could ever run --
        # incorrectly treating "send this to arbitration" as "the claimant
        # lost on the merits", which misrepresents what actually happened.
        relief_kind = requested_relief
    if relief_kind in _NO_INCIDENTAL_AMOUNT_KINDS:
        # An arbitration referral means this forum isn't deciding the
        # merits at all -- it cannot simultaneously award incidental
        # damages, unlike injunction/declaration/replacement/possession
        # which legitimately can carry one alongside the primary relief.
        recommended_amount = 0.0
    n_prec = len(precedents)
    full_relief = sum(1 for r in ratios if r >= 0.99)
    pct_full = round(100 * full_relief / len(ratios))
    interest_rate_pct = _DEFAULT_INTEREST_RATE_PCT if (relief_kind != "dismissed" and recommended_amount > 0) else 0.0

    non_monetary = relief_kind in _NON_MONETARY_KINDS

    def _relief_phrase() -> str:
        # "injunction of Rs. X" reads wrong -- a non-monetary kind is the
        # relief itself, with any recommended_amount being a SEPARATE,
        # incidental monetary component alongside it (e.g. injunction +
        # damages), not "X worth of injunction".
        if non_monetary:
            base = _NON_MONETARY_KINDS[relief_kind]
            if recommended_amount > 0:
                return f"{base}, plus incidental compensation of {nlp.inr(recommended_amount)}"
            return base
        return f"{nlp.monetary_relief_phrase(relief_kind, ctx.dispute_type)} of {nlp.inr(recommended_amount)}"

    # Report only what we can actually source: the percentage is computed over the
    # precedents that were genuinely retrieved for THIS case (no fabricated cohort).
    dismissed = relief_kind == "dismissed"
    if dismissed:
        headline = (
            f"Across {n_prec} closely-matched precedent{'s' if n_prec != 1 else ''}, "
            f"{pct_full}% awarded full relief. On the record here, the respondent's position is at "
            "least as strong as the claimant's -- no relief is recommended."
        )
    else:
        interest_phrase = f" plus {interest_rate_pct:g}% p.a. interest" if interest_rate_pct else ""
        headline = (
            f"Across {n_prec} closely-matched precedent{'s' if n_prec != 1 else ''}, "
            f"{pct_full}% awarded full relief. Recommended resolution: "
            f"{_relief_phrase()}{interest_phrase}, within {compliance_days} days."
        )
    if not explanation:
        explanation = (
            "The claimant has not shown a case stronger than the respondent's on the record; no "
            "relief is recommended."
            if dismissed
            else (
                f"{_relief_phrase()[0].upper()}{_relief_phrase()[1:]}"
                + (f" (plus {interest_rate_pct:g}% per annum simple interest until payment)" if interest_rate_pct else "")
                + f" within {compliance_days} days is consistent with how comparable disputes were resolved."
            )
        )

    rationale = [
        f"{len(precedents)} closely-matched precedents analysed ({ctx.research.method if ctx.research else 'n/a'} retrieval).",
        f"Claimant case strength assessed at {int(c_strength * 100)}%, respondent at {int(r_strength * 100)}%.",
        (
            "No relief recommended: the respondent's position is at least as strong as the claimant's."
            if dismissed
            else f"Relief set at {int(clamped_ratio * 100)}% of the claim, within the "
            f"{int(prec_min*100)}–{int(prec_max*100)}% precedent band."
        ),
        f"Median compliance window in precedent: {median_days} days.",
    ]
    if interest_rate_pct:
        rationale.append(f"Simple interest at {interest_rate_pct:g}% per annum from the date of this order until payment.")
    if precedents:
        rationale.append(f"Leading authority: {precedents[0].citation}.")

    proposal = MediationProposal(
        type=relief_kind,
        amount=recommended_amount,
        amount_display=nlp.inr(recommended_amount),
        compliance_days=compliance_days,
        interest_rate_pct=interest_rate_pct,
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
        f"({int(clamped_ratio*100)}% of claim)"
        + (f" plus {interest_rate_pct:g}% p.a. interest" if interest_rate_pct else "")
        + f" within {compliance_days} days. "
        + (f"{len(validator_notes)} validator adjustment(s)." if validator_notes else "Within precedent band.")
    )
    return AgentResult(output=proposal, detail=detail, confidence=proposal.confidence, citations=proposal.based_on, engine=engine)


def _ratio_to_type(ratio: float) -> str:
    if ratio < 0.05:
        return "dismissed"
    if ratio >= 0.99:
        return "full_refund"
    if ratio >= 0.5:
        return "partial_refund"
    return "compensation"
