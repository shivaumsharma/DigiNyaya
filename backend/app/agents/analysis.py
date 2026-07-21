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

    # LLM reasoning pass -- single call covering both the neutral narrative
    # AND (for genuinely contested cases) a real judgment of how dispositive
    # the respondent's defense actually is, instead of only the keyword
    # heuristic (nlp.score_defense_substance). Run BEFORE strength scoring so
    # the contested branch below can use it. Real-judgment testing at 201
    # cases found the keyword heuristic has a real ceiling: a short,
    # LLM-compressed defense summary sometimes states the underlying facts
    # without naming the legal doctrine a keyword scan looks for, so a
    # genuinely dispositive defense (e.g. limitation, no privity of contract)
    # scored no differently from a bare denial. An LLM reading the actual
    # defense text can assess substance directly rather than pattern-match
    # for specific phrases. nlp.score_defense_substance stays as the
    # fallback when the LLM is unavailable or the call fails -- this agent
    # must never hard-depend on a live model.
    engine = "scripted"
    neutral = None
    llm_defense_strength: float | None = None
    r_text = respondent.get("statement", "") if respondent else "(No response filed within the 72-hour window.)"
    schema = (
        '{"neutral_summary": "<max 70 word neutral paragraph for a quasi-judicial record, do not take '
        'sides, use only the facts given>", '
        '"respondent_defense_strength": <number 0.0-1.0: how dispositive/case-ending the RESPONDENT\'s '
        "defense is on its own legal merits -- 0.0 means a bare denial with no real substance, 1.0 means "
        "a fully dispositive ground such as limitation, lack of jurisdiction, no privity of contract, res "
        "judicata, arbitration clause, or the claimant's own case failing to establish an essential fact. "
        "Judge the SUBSTANCE of the argument, not merely whether a defense was filed at all.>"
        "}"
    )
    prompt = (
        f"Analyse this {nlp.dispute_label(ctx.dispute_type)} for a quasi-judicial record. Return JSON only, "
        f"matching this schema: {schema}\n\n"
        f"Claim amount: {ing.claim_amount_display if ing else nlp.inr(ctx.claim_amount)}\n"
        f"{wrap_untrusted('CLAIMANT_STATEMENT', ctx.description)}\n"
        f"{wrap_untrusted('RESPONDENT_STATEMENT', r_text)}\n"
        f"Evidence on record: {ev} item(s)."
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=500)
    if data and data.get("neutral_summary"):
        neutral = str(data["neutral_summary"]).strip()
        engine = "llm"
        try:
            llm_defense_strength = float(data.get("respondent_defense_strength"))
            llm_defense_strength = min(max(llm_defense_strength, 0.0), 1.0)
        except (TypeError, ValueError):
            llm_defense_strength = None

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
        #
        # Evidence-count weight: 0.2/item originally saturated c_score to
        # ~0.8 for the 75% of real cases with evidence_count>=3 regardless of
        # actual merit; 0.13/item (ceiling ~0.59) is the measured best state
        # (31% exact / 49% directional over 150 scored real-judgment cases).
        # Two further recalibration attempts at 0.16/item and the 0.145/item
        # midpoint between them were BOTH tried and both scored worse (23-27%
        # exact) -- reverted back to 0.13 rather than keep guessing via
        # expensive paid re-judges with no clear signal left to chase. See
        # scripts/judge_real_outcomes.py's cached results and
        # [[diginyaya_real_judgment_eval]] memory for the full trail.
        c_score = round(min(0.2 + min(ev, 3) * 0.13, 0.85), 2)
        # Respondent's strength depends on TWO things, not just the
        # counter-offer: how much they concede via a counter-offer, AND how
        # substantive/specific the defense itself is (a dispositive ground
        # like "no contract existed" or "barred by limitation" vs a bare
        # denial). Previously only the counter-offer mattered, so a
        # rock-solid defense and a weak denial scored identically -- see
        # nlp.score_defense_substance for why that was the dominant
        # remaining real-judgment failure mode.
        #
        # 0.45 baseline + 0.4 slope is the measured best state (see above) --
        # reverted here for the same reason as c_score's evidence weight.
        #
        # Prefer the LLM's own reading of the defense (llm_defense_strength,
        # computed above) when the call succeeded -- it can judge substance
        # directly rather than pattern-match specific phrases. Falls back to
        # the keyword heuristic when the LLM is unavailable or failed.
        defense_score = (
            llm_defense_strength if llm_defense_strength is not None
            else nlp.score_defense_substance(respondent.get("statement", ""))
        )
        counter = respondent.get("counter_offer")
        if counter is None or counter <= 0:
            r_score = round(0.45 + defense_score * 0.4, 2)
        else:
            concession = min(counter / ctx.claim_amount, 1.0) if ctx.claim_amount else 0.0
            r_score = round(0.45 + defense_score * 0.4 - concession * 0.35, 2)
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

    # Falls back to the scripted narrative if the combined LLM call above
    # didn't run/succeed (neutral is still None in that case).
    if neutral is None:
        neutral = _scripted_summary(ctx, c_label, r_label)

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
