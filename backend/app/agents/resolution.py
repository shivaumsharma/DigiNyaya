"""Agent 5 — Resolution Drafting Agent.

Drafts the binding order. The LLM writes the reasoned *findings*; the operative
order, amounts and deadline are fully deterministic. Citations are verified
against what Research actually retrieved (any unsupported reference is dropped).
Escalated (Tier 2) cases are marked as requiring human sign-off.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .. import llm, rag
from ..core import confidence as confidence_module
from ..core.context import CaseContext, ResolutionDoc
from . import nlp
from .base import AgentResult

# Action phrase for each non-monetary relief kind mediation.py may set
# (see app.agents.nlp.detect_relief_type / mediation._NON_MONETARY_KINDS).
# Filled in as the object of "The respondent shall ___" / "It is recommended
# that the respondent ___" -- see finalize() below.
_NON_MONETARY_ACTIONS = {
    "injunction": "cease and/or reverse the conduct complained of by the claimant, as detailed in the findings above",
    "declaration": (
        "treat the claimant's position as set out in the findings above as upheld, and any contrary "
        "instrument or action asserted by the respondent as of no legal effect"
    ),
    "replacement": "provide the claimant a replacement of like kind and quality for the goods/services in dispute",
    "possession": "hand over vacant possession of the property in dispute to the claimant",
    "partition": "recognise the claimant's declared share in the property and cooperate in effecting a partition by metes and bounds accordingly",
    "reinstatement": "reinstate the claimant to their former position with continuity of service",
    "arbitration_referral": (
        "refer the parties to arbitration in accordance with the arbitration clause between them, without "
        "adjudication of the merits of this dispute by this forum"
    ),
}


def findings_prompt(ctx: CaseContext) -> str:
    precedents = ctx.research.precedents if ctx.research else []
    med = ctx.mediation
    amount = med.amount if med else ctx.claim_amount
    compliance_days = med.compliance_days if med else 30
    subtype = ctx.ingestion.dispute_subtype if ctx.ingestion else nlp.dispute_label(ctx.dispute_type)
    lead = precedents[0].citation if precedents else "the cited authorities"
    dismissed = bool(med and med.type == "dismissed")
    outcome_line = (
        "Outcome: the claimant's case is DISMISSED -- no relief awarded, the respondent's position "
        "prevails on the record."
        if dismissed
        else f"Outcome: relief of {nlp.inr(amount)} payable within {compliance_days} days."
    )
    return (
        f"Draft the 'Findings' section of a {nlp.dispute_label(ctx.dispute_type)} resolution order as 3 to 4 "
        "numbered sentences. Neutral, formal, quasi-judicial tone. Use ONLY these facts; do not invent "
        "amounts, dates or citations.\n\n"
        f"Claimant: {ctx.claimant_name}\nRespondent: {ctx.respondent_name}\nGrievance: {subtype}\n"
        f"Claim amount: {nlp.inr(ctx.claim_amount)}\n"
        f"Evidence items: {len(ctx.evidence)}\n"
        f"Respondent responded: {'no, uncontested' if ctx.respondent_submission is None else 'yes'}\n"
        f"Leading precedent: {lead}\n"
        f"{outcome_line}"
    )


def _select_citations(ctx: CaseContext, precedents: list) -> tuple[list[str], str]:
    """Decide which retrieved precedents to cite.

    When the LLM is available, this is a genuine test rather than a tautology:
    the model is shown a mixed pool of the real retrieved precedents PLUS a
    couple of decoys sampled from the corpus that were NOT retrieved for this
    case, and asked to pick which ones support the outcome. Citations are then
    verified against what Research actually retrieved — if the model picks a
    decoy (or invents something outside the pool entirely), verification drops
    it, and that's a real, measurable rejection.

    Without the LLM (or if it fails/parses badly), we fall back to the prior
    deterministic behaviour: cite the top-3 retrieved precedents outright.
    """
    if not precedents:
        return [], "scripted"

    deterministic = [p.id for p in precedents[:3]]
    if not llm.is_available():
        return deterministic, "scripted"

    decoys = rag.decoy_candidates([p.id for p in precedents], ctx.dispute_type, k=2)
    pool = list(precedents[:5]) + [
        type(precedents[0])(
            id=d["id"], title=d["title"], court=d["court"], year=d["year"], citation=d["citation"],
            summary=d["summary"], principle=d["principle"], outcome=d["outcome"],
            outcome_detail=d["outcome_detail"], relief_amount_ratio=d["relief_amount_ratio"],
            compliance_days=d["compliance_days"], relevance=0.0, matched_signals=[],
        )
        for d in decoys
    ]
    import random
    random.shuffle(pool)

    candidates_text = "\n".join(f"- id={c.id} | {c.citation} | {c.principle}" for c in pool)
    schema = '{"cited_ids": ["<id>", "<id>", ...]}'
    prompt = (
        "Below is a pool of precedent candidates. Some were retrieved as relevant to this case; "
        "others were not and should NOT be cited. Select ONLY the id(s) (at most 3) that genuinely "
        f"support the outcome for this dispute. Return JSON only, matching this schema: {schema}\n\n"
        f"Dispute subtype: {ctx.ingestion.dispute_subtype if ctx.ingestion else nlp.dispute_label(ctx.dispute_type)}\n"
        f"Claim amount: {nlp.inr(ctx.claim_amount)}\n\n"
        f"Candidates:\n{candidates_text}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=500)
    if not data or not isinstance(data.get("cited_ids"), list):
        return deterministic, "scripted"

    proposed = [str(cid) for cid in data["cited_ids"] if isinstance(cid, (str, int))][:3]
    return (proposed or deterministic), "llm"


def run(ctx: CaseContext) -> AgentResult:
    """Non-streaming path: generate findings then finalize."""
    out = llm.generate(findings_prompt(ctx), system=llm.SYSTEM_PROMPT, max_tokens=600)
    return finalize(ctx, out)


def compute_composite_confidence(ctx: CaseContext) -> tuple[dict, list[dict], list[str], str]:
    """Citation selection/verification + the composite confidence score --
    pulled out of finalize() so the orchestrator can safety-gate-check
    (app.core.safety_gate condition 2) BEFORE spending time on the slow
    LLM-streamed findings draft in resolve_node. None of this depends on the
    findings text itself, only on ingestion/research/analysis/mediation
    outputs that are already on the blackboard by this point."""
    precedents = ctx.research.precedents if ctx.research else []
    med = ctx.mediation
    retrieved_ids = [p.id for p in precedents]
    proposed_ids, citation_engine = _select_citations(ctx, precedents)
    cite_ids = rag.verify_citations(proposed_ids, retrieved_ids)
    cited = [
        {"citation": p.citation, "principle": p.principle}
        for p in precedents if p.id in cite_ids
    ]
    composite = confidence_module.composite(
        ingestion_confidence=ctx.ingestion.confidence if ctx.ingestion else 0.5,
        retrieval_coverage=ctx.research.coverage_score if ctx.research else 0.5,
        validator_notes=med.validator_notes if med else [],
        n_proposed_citations=len(proposed_ids),
        n_verified_citations=len(cite_ids),
        citation_engine=citation_engine,
    )
    return composite, cited, cite_ids, citation_engine


def finalize(
    ctx: CaseContext,
    findings_text: str | None,
    precomputed: tuple[dict, list[dict], list[str], str] | None = None,
) -> AgentResult:
    """Build the order from (optional) LLM findings text + deterministic figures.

    ``precomputed`` is the (composite, cited, cite_ids, citation_engine) tuple
    from compute_composite_confidence(), when the caller already ran it for
    the early safety-gate check -- avoids a second (possibly LLM-backed)
    citation selection call."""
    med = ctx.mediation
    amount = med.amount if med else ctx.claim_amount
    compliance_days = med.compliance_days if med else 30
    claimant, respondent = ctx.claimant_name, ctx.respondent_name
    via_mediation = ctx.via_mediation
    requires_signoff = bool(ctx.route and ctx.route.requires_human_signoff)

    today = datetime.utcnow().date()
    deadline = today + timedelta(days=compliance_days)

    composite, cited, cite_ids, _citation_engine = precomputed or compute_composite_confidence(ctx)

    subtype = ctx.ingestion.dispute_subtype if ctx.ingestion else nlp.dispute_label(ctx.dispute_type)
    dismissed = bool(med and med.type == "dismissed")
    basis = (
        "the claimant not having established, on the record, an entitlement to relief"
        if dismissed
        else "by mutual consent following AI-facilitated mediation"
        if via_mediation
        else "as a non-binding AI recommendation, mediation having been declined"
    )

    engine = "scripted"
    findings = _scripted_findings(ctx, subtype, amount, compliance_days, dismissed=dismissed)
    if findings_text:
        parsed = _split(findings_text)
        if parsed:
            findings, engine = parsed, "llm"

    # Only a consensual (mediated) settlement carries binding, enforceable language.
    binding = via_mediation and not requires_signoff
    relief_for = nlp.monetary_relief_phrase(med.type, ctx.dispute_type) if med else "relief"
    interest_rate = med.interest_rate_pct if med else 0.0
    interest_clause = (
        f" plus simple interest at {interest_rate:g}% per annum on that sum from the date of this "
        "order until payment is made in full"
        if interest_rate
        else ""
    )
    non_monetary_action = _NON_MONETARY_ACTIONS.get(med.type) if med else None
    if dismissed:
        order = [
            f"{claimant}'s case is dismissed for want of a stronger showing than {respondent}'s "
            "position on the record.",
            "No payment is ordered. Either party may bring fresh evidence for a renewed hearing.",
        ]
    elif non_monetary_action:
        # The real ask (and what a real court would order) here is NOT
        # primarily money -- e.g. an injunction, a declaration, a
        # replacement in kind, or restoring possession. Forcing every
        # outcome into "pay the claimant Rs. X" was confirmed, against real
        # judgments, to misrepresent the actual relief in ~1 of every 4
        # non-dismissed cases. `amount` (if > 0) is drafted as a SEPARATE,
        # incidental line -- claims routinely seek both (e.g. injunction +
        # damages), never as a substitute for the primary non-monetary order.
        order = [
            f"{respondent} shall {non_monetary_action}."
            if binding
            else f"It is recommended that {respondent} {non_monetary_action}.",
            f"Compliance is due on or before {deadline.isoformat()} ({compliance_days} days).",
        ]
        if amount > 0:
            order.append(
                f"In addition, {respondent} shall pay {claimant} {nlp.inr(amount)} towards incidental "
                f"compensation{interest_clause}."
                if binding
                else f"In addition, it is recommended that {respondent} pay {claimant} {nlp.inr(amount)} "
                f"towards incidental compensation{interest_clause}."
            )
        order.append(
            "Compliance shall be reported through the DigiNyaya portal; non-compliance will trigger "
            "an automatic escalation notice and enforcement reference."
            if binding
            else "This recommendation becomes enforceable only upon the parties' written consent or, where "
            "applicable, after human adjudication."
        )
    elif binding:
        order = [
            f"{respondent} shall pay {claimant} {nlp.inr(amount)} towards {relief_for}{interest_clause}.",
            f"Payment shall be completed on or before {deadline.isoformat()} ({compliance_days} days from this agreement).",
            "Compliance shall be reported through the DigiNyaya portal; non-compliance will trigger "
            "an automatic escalation notice and enforcement reference.",
        ]
    else:
        order = [
            f"It is recommended that {respondent} pay {claimant} {nlp.inr(amount)} towards {relief_for}{interest_clause}.",
            f"The suggested compliance window is on or before {deadline.isoformat()} ({compliance_days} days).",
            "This recommendation becomes enforceable only upon the parties' written consent or, where "
            "applicable, after human adjudication.",
        ]
    if requires_signoff:
        order.append("This recommendation is provisional pending review and counter-signature by the assigned human adjudicator (Tier 2).")

    doc = ResolutionDoc(
        header="DIGINYAYA — ONLINE DISPUTE RESOLUTION",
        subheader=(
            "Provisional Resolution Order (Tier 2 — AI-Assisted, Human Sign-off Pending)"
            if requires_signoff
            else "Resolution — Claim Dismissed (Tier 1)"
            if dismissed
            else "Settlement Agreement (Tier 1 — binding on the parties' consent under the Mediation Act, 2023)"
            if via_mediation
            else "Recommended Resolution (Tier 1 — non-binding, pending the parties' consent)"
        ),
        case_id=ctx.case_id,
        date=today.isoformat(),
        parties={"claimant": claimant, "respondent": respondent},
        basis=basis,
        claim_amount_display=nlp.inr(ctx.claim_amount),
        findings=findings,
        order=order,
        cited_precedents=cited,
        relief_amount=amount,
        relief_amount_display=nlp.inr(amount),
        compliance_days=compliance_days,
        compliance_deadline=deadline.isoformat(),
        via_mediation=via_mediation,
        requires_human_signoff=requires_signoff,
        engine=engine,
        composite_confidence=composite,
        footer=(
            "Issued under the Online Dispute Resolution framework. This resolution is generated "
            "by DigiNyaya's AI pipeline and does not constitute a court order or independent "
            "legal advice."
        ),
    )

    kind = (
        "provisional (Tier 2)" if requires_signoff
        else "dismissal" if dismissed
        else "binding consent settlement" if binding
        else "non-binding recommendation"
    )
    rejected = composite["citations_rejected"]
    detail = (
        f"Drafted {kind}: "
        + ("no relief awarded" if dismissed else f"{nlp.inr(amount)} within {compliance_days} days")
        + f", citing {len(cited)} verified precedent(s)"
        + (f" ({rejected} proposed citation(s) rejected as unretrieved)" if rejected else "")
        + f". Composite confidence {int(composite['score'] * 100)}%."
        + ("" if dismissed else f" Deadline {deadline.isoformat()}.")
    )
    return AgentResult(output=doc, detail=detail, confidence=composite["score"], citations=cite_ids, engine=engine)


def _scripted_findings(ctx: CaseContext, subtype: str, amount: float, days: int, *, dismissed: bool = False) -> list[str]:
    if dismissed:
        return [
            f"The claimant, {ctx.claimant_name}, asserted a case of '{subtype}' "
            f"supported by {len(ctx.evidence)} item(s) of evidence.",
            f"The respondent, {ctx.respondent_name}, "
            + (
                "did not respond, but the claimant's own record does not establish the claim on the "
                "balance of the facts presented."
                if ctx.respondent_submission is None
                else "disputed the claim, and the response is at least as well-supported as the claimant's case."
            ),
            "On the facts and evidence presented, the claimant has not shown an entitlement to relief; "
            "the case is dismissed without prejudice to fresh evidence.",
        ]
    return [
        f"The claimant, {ctx.claimant_name}, established a prima facie case of '{subtype}' "
        f"supported by {len(ctx.evidence)} item(s) of evidence.",
        f"The respondent, {ctx.respondent_name}, "
        + (
            "failed to respond within the stipulated 72-hour window and the allegations stand substantially uncontested."
            if ctx.respondent_submission is None
            else "filed a response which did not displace the claimant's documented case."
        ),
        "Applying the settled principles in the precedents cited below, the claimant is entitled to relief.",
    ]


def _split(text: str) -> list[str]:
    lines = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if line]
    if len(lines) <= 1:
        lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return lines[:5]
