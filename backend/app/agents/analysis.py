"""Agent 3 — Argument Analysis Agent.

Produces a neutral two-sided summary and, crucially, a *strength score* that
feeds the Mediation agent's quantum (so this agent is no longer cosmetic). It
also decides whether precedent coverage is too thin, which the orchestrator
uses to loop back to Research.
"""

from __future__ import annotations

from .. import llm
from ..core.context import AnalysisResult, CaseContext
from . import nlp
from .base import AgentResult, wrap_untrusted

_POSITION_LABELS = {
    "non_delivery": "Alleges paid goods were never delivered.",
    "defective_product": "Alleges the product is defective / not functioning.",
    "counterfeit": "Alleges the product received is counterfeit.",
    "misrepresentation": "Alleges the product was misrepresented / not as described.",
    "service_deficiency": "Alleges deficiency of service by the respondent.",
    "wrongful_billing": "Alleges wrongful or excess billing.",
    "unauthorized_transaction": "Alleges an unauthorized transaction.",
    "subscription": "Alleges an unwanted auto-renewal charge.",
    "repair_delay": "Alleges unreasonable delay in warranty repair.",
    "refund": "Seeks a refund of the amount paid.",
    "loan_default": "Alleges non-repayment of a loan / money lent.",
    "unpaid_dues": "Alleges non-payment of outstanding dues.",
    "cheque_dishonour": "Alleges the cheque issued towards repayment was dishonoured.",
    "breach_of_agreement": "Alleges breach of a written agreement's terms.",
}


def run(ctx: CaseContext) -> AgentResult:
    ing = ctx.ingestion
    research = ctx.research
    respondent = ctx.respondent_submission

    claimant_points = [_POSITION_LABELS[s] for s in (ing.signals if ing else []) if s in _POSITION_LABELS]
    if not claimant_points:
        claimant_points = ["Asserts a grievance seeking redress."]

    contradictions: list[str] = []
    respondent_points: list[str] = []

    # Computed unconditionally (not just inside the narrative block below)
    # since the strength-scoring if/elif chain further down also branches on
    # it, and `respondent` being a non-None-but-falsy dict would otherwise
    # leave it undefined there.
    defaulted = bool(respondent) and not respondent.get("accepts_liability", False) and nlp.defendant_defaulted(
        respondent.get("statement", "")
    )

    if respondent:
        accepts = respondent.get("accepts_liability", False)
        counter = respondent.get("counter_offer")
        if accepts:
            respondent_points.append("Respondent broadly accepts liability.")
        elif defaulted:
            respondent_points.append("Respondent's own submission shows no defense was actually put forward.")
        else:
            respondent_points.append("Respondent disputes the claim as presented.")
        if counter is not None:
            respondent_points.append(f"Respondent proposes a counter-settlement of {nlp.inr(counter)}.")
            if counter < ctx.claim_amount:
                contradictions.append(
                    f"Quantum gap: claimant seeks {nlp.inr(ctx.claim_amount)} but respondent offers only {nlp.inr(counter)}."
                )
    else:
        respondent_points.append(
            "Respondent did not respond within 72 hours; allegations are substantially uncontested."
        )

    # Strength scoring (0..1) — drives mediation quantum.
    #
    # IMPORTANT: claimant strength must NOT have a fixed floor. A previous
    # version started every case at 0.5 regardless of evidence or contest,
    # which meant a genuinely weak, contested, thinly-evidenced claim scored
    # exactly the same "at least moderate" as a strong one — the downstream
    # mediation validator then clamped relief to a nonzero floor no matter
    # what, so the system could structurally never recommend "the claimant
    # is not entitled to relief". Verified against 46 real court judgments:
    # the AI matched the real court's result in 0/36 scored cases, and the
    # dominant failure mode was exactly this — awarding relief the real
    # court had refused entirely. See scripts/judge_real_outcomes.py.
    ev = ing.evidence_count if ing else 0

    if respondent is None:
        # Uncontested: claimant's version stands unopposed.
        c_score = round(min(0.55 + min(ev, 3) * 0.13, 0.97), 2)
        r_score = 0.15
    elif respondent.get("accepts_liability"):
        # Respondent concedes -- claimant's case is essentially proven.
        c_score = round(min(0.6 + min(ev, 3) * 0.12, 0.97), 2)
        r_score = 0.15
    elif defaulted:
        # Respondent's own record shows they never engaged with the
        # proceeding at all (ex-parte / no defense filed) -- score like the
        # uncontested case above, not a generic firm denial. Real courts
        # almost always decree for the claimant by default here.
        c_score = round(min(0.55 + min(ev, 3) * 0.13, 0.97), 2)
        r_score = 0.15
    else:
        # Genuinely contested: claimant strength scales with evidence on
        # record from a genuinely low floor (0.2, no evidence at all) rather
        # than assuming a moderate case by default.
        c_score = round(min(0.2 + min(ev, 3) * 0.2, 0.85), 2)
        # Respondent's strength depends on TWO things, not just the
        # counter-offer: how much they concede via a counter-offer, AND how
        # substantive/specific the defense itself is (a dispositive ground
        # like "no contract existed" or "barred by limitation" vs a bare
        # denial). Previously only the counter-offer mattered, so a
        # rock-solid defense and a weak denial scored identically -- see
        # nlp.score_defense_substance for why that was the dominant
        # remaining real-judgment failure mode.
        defense_score = nlp.score_defense_substance(respondent.get("statement", ""))
        counter = respondent.get("counter_offer")
        if counter is None or counter <= 0:
            r_score = round(0.4 + defense_score * 0.45, 2)
        else:
            concession = min(counter / ctx.claim_amount, 1.0) if ctx.claim_amount else 0.0
            r_score = round(0.4 + defense_score * 0.45 - concession * 0.35, 2)
        r_score = round(min(max(r_score, 0.2), 0.85), 2)

    c_score = round(min(max(c_score, 0.1), 0.97), 2)

    c_label = "strong" if c_score >= 0.75 else "moderate" if c_score >= 0.5 else "weak"
    r_label = "strong" if r_score >= 0.6 else "moderate" if r_score >= 0.35 else "weak"

    # Loop-back trigger: thin coverage and we haven't already retried.
    coverage = research.coverage_label if research else "thin"
    needs_more = coverage == "thin" and ctx.research_retries < 1

    undisputed = [
        f"A transaction of {ing.claim_amount_display} took place between the parties." if ing else "",
        f"The dispute concerns: {ing.dispute_subtype}." if ing else "",
    ]
    undisputed = [u for u in undisputed if u]

    # LLM neutral narrative (untrusted party text fenced); scripted fallback.
    engine = "scripted"
    neutral = _scripted_summary(ctx, c_label, r_label)
    r_text = respondent.get("statement", "") if respondent else "(No response filed within the 72-hour window.)"
    prompt = (
        f"Write a single neutral paragraph (max 70 words) summarising this {nlp.dispute_label(ctx.dispute_type)} "
        "for a quasi-judicial record. Do not take sides. Use only these facts.\n\n"
        f"Dispute type: {ing.dispute_subtype if ing else 'consumer grievance'}\n"
        f"Claim amount: {ing.claim_amount_display if ing else nlp.inr(ctx.claim_amount)}\n"
        f"{wrap_untrusted('CLAIMANT_STATEMENT', ctx.description)}\n"
        f"{wrap_untrusted('RESPONDENT_STATEMENT', r_text)}\n"
        f"Evidence on record: {ev} item(s). Assessed strength — claimant: {c_label}, respondent: {r_label}."
    )
    out = llm.generate(prompt, system=llm.SYSTEM_PROMPT, max_tokens=160)
    if out:
        neutral = out
        engine = "llm"

    result = AnalysisResult(
        claimant_position=claimant_points,
        respondent_position=respondent_points,
        undisputed_facts=undisputed,
        contradictions=contradictions or ["No direct factual contradictions detected."],
        neutral_summary=neutral,
        strength={"claimant": c_label, "respondent": r_label},
        strength_score={"claimant": c_score, "respondent": r_score},
        needs_more_research=needs_more,
        confidence=round((c_score + (research.coverage_score if research else 0.5)) / 2, 2),
        engine=engine,
    )

    detail = (
        f"Neutral summary generated. Claimant '{c_label}', respondent '{r_label}'. "
        f"Flagged {len(result.contradictions)} point(s). "
        + ("Precedent coverage thin — requesting broader research." if needs_more else f"Precedent coverage: {coverage}.")
    )
    return AgentResult(output=result, detail=detail, confidence=result.confidence, engine=engine)


def _scripted_summary(ctx: CaseContext, c_label: str, r_label: str) -> str:
    sub = ctx.ingestion.dispute_subtype.lower() if ctx.ingestion else "a consumer grievance"
    amt = ctx.ingestion.claim_amount_display if ctx.ingestion else nlp.inr(ctx.claim_amount)
    contested = (
        "The respondent has not contested the allegations. "
        if ctx.respondent_submission is None
        else "The respondent disputes the claim. "
    )
    return (
        f"The claimant {ctx.claimant_name} alleges {sub} concerning a transaction of {amt} "
        f"with {ctx.respondent_name}. {contested}"
        f"On the record, the claimant's case appears {c_label} and the respondent's {r_label}."
    )
