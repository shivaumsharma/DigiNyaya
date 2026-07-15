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


def findings_prompt(ctx: CaseContext) -> str:
    precedents = ctx.research.precedents if ctx.research else []
    med = ctx.mediation
    amount = med.amount if med else ctx.claim_amount
    compliance_days = med.compliance_days if med else 30
    subtype = ctx.ingestion.dispute_subtype if ctx.ingestion else "consumer grievance"
    lead = precedents[0].citation if precedents else "the cited authorities"
    return (
        "Draft the 'Findings' section of a consumer dispute resolution order as 3 to 4 numbered "
        "sentences. Neutral, formal, quasi-judicial tone. Use ONLY these facts; do not invent "
        "amounts, dates or citations.\n\n"
        f"Claimant: {ctx.claimant_name}\nRespondent: {ctx.respondent_name}\nGrievance: {subtype}\n"
        f"Claim amount: {nlp.inr(ctx.claim_amount)}\n"
        f"Evidence items: {len(ctx.evidence)}\n"
        f"Respondent responded: {'no, uncontested' if ctx.respondent_submission is None else 'yes'}\n"
        f"Leading precedent: {lead}\n"
        f"Outcome: relief of {nlp.inr(amount)} payable within {compliance_days} days."
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

    decoys = rag.decoy_candidates([p.id for p in precedents], k=2)
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
        f"Dispute subtype: {ctx.ingestion.dispute_subtype if ctx.ingestion else 'consumer grievance'}\n"
        f"Claim amount: {nlp.inr(ctx.claim_amount)}\n\n"
        f"Candidates:\n{candidates_text}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=160)
    if not data or not isinstance(data.get("cited_ids"), list):
        return deterministic, "scripted"

    proposed = [str(cid) for cid in data["cited_ids"] if isinstance(cid, (str, int))][:3]
    return (proposed or deterministic), "llm"


def run(ctx: CaseContext) -> AgentResult:
    """Non-streaming path: generate findings then finalize."""
    out = llm.generate(findings_prompt(ctx), system=llm.SYSTEM_PROMPT, max_tokens=260)
    return finalize(ctx, out)


def finalize(ctx: CaseContext, findings_text: str | None) -> AgentResult:
    """Build the order from (optional) LLM findings text + deterministic figures."""
    precedents = ctx.research.precedents if ctx.research else []
    med = ctx.mediation
    amount = med.amount if med else ctx.claim_amount
    compliance_days = med.compliance_days if med else 30
    claimant, respondent = ctx.claimant_name, ctx.respondent_name
    via_mediation = ctx.via_mediation
    requires_signoff = bool(ctx.route and ctx.route.requires_human_signoff)

    today = datetime.utcnow().date()
    deadline = today + timedelta(days=compliance_days)

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

    subtype = ctx.ingestion.dispute_subtype if ctx.ingestion else "consumer grievance"
    basis = (
        "by mutual consent following AI-facilitated mediation"
        if via_mediation
        else "as a non-binding AI recommendation, mediation having been declined"
    )

    engine = "scripted"
    findings = _scripted_findings(ctx, subtype, amount, compliance_days)
    if findings_text:
        parsed = _split(findings_text)
        if parsed:
            findings, engine = parsed, "llm"

    # Only a consensual (mediated) settlement carries binding, enforceable language.
    binding = via_mediation and not requires_signoff
    relief_for = med.type.replace("_", " ") if med else "relief"
    if binding:
        order = [
            f"The respondent shall pay the claimant {nlp.inr(amount)} towards {relief_for}.",
            f"Payment shall be completed on or before {deadline.isoformat()} ({compliance_days} days from this agreement).",
            "Compliance shall be reported through the DigiNyaya portal; non-compliance will trigger "
            "an automatic escalation notice and enforcement reference.",
        ]
    else:
        order = [
            f"It is recommended that the respondent pay the claimant {nlp.inr(amount)} towards {relief_for}.",
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
            "Issued under the Online Dispute Resolution framework. This is a demonstration "
            "order generated by DigiNyaya for a hackathon prototype and does not constitute a "
            "court order or legal advice."
        ),
    )

    kind = "provisional (Tier 2)" if requires_signoff else "binding consent settlement" if binding else "non-binding recommendation"
    rejected = composite["citations_rejected"]
    detail = (
        f"Drafted {kind}: {nlp.inr(amount)} within {compliance_days} days, "
        f"citing {len(cited)} verified precedent(s)"
        + (f" ({rejected} proposed citation(s) rejected as unretrieved)" if rejected else "")
        + f". Composite confidence {int(composite['score'] * 100)}%. Deadline {deadline.isoformat()}."
    )
    return AgentResult(output=doc, detail=detail, confidence=composite["score"], citations=cite_ids, engine=engine)


def _scripted_findings(ctx: CaseContext, subtype: str, amount: float, days: int) -> list[str]:
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
    lines = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", l).strip() for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if l]
    if len(lines) <= 1:
        lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return lines[:5]
