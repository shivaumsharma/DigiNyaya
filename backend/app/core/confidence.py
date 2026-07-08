"""Composite confidence score.

Ingestion confidence answers "how complete was the input". That's not the
same question as "how trustworthy is the final output" — the gap this module
closes. It combines four signals that are already computed elsewhere in the
pipeline into one number that reflects the *whole* run, not just Agent 1:

  ingestion (0.35) — Agent 1's classification confidence (evidence on record,
                      a quantified claim, a recognised subtype).
  retrieval (0.35) — Agent 2's coverage_score: how well the precedent corpus
                      actually covers this case.
  schema    (0.20) — how much the deterministic validator in Agent 4 had to
                      correct the model's structured mediation proposal. A
                      proposal that needed no clamping is more trustworthy
                      than one the validator had to rescue.
  citation  (0.10) — the fraction of citations Agent 5 proposed that actually
                      survived verification against what Agent 2 retrieved
                      (see rag.verify_citations). A high rejection rate here
                      is a direct hallucination signal.

The composite is assembled once in Agent 5 (resolution.finalize), after every
upstream agent has run, so it reflects the complete case rather than any
single step.
"""

from __future__ import annotations

WEIGHTS = {
    "ingestion": 0.35,
    "retrieval": 0.35,
    "schema": 0.20,
    "citation": 0.10,
}


def schema_score(validator_notes: list[str]) -> float:
    """1.0 if Agent 4's structured output needed no correction. Each
    correction the deterministic validator had to apply (an out-of-band
    relief ratio, a non-standard compliance window, ...) costs 0.15, floored
    at 0.4 — the validator caught it, so the case is still usable, but it
    shouldn't score as trustworthy as one that needed no rescue at all."""
    return round(max(1.0 - 0.15 * len(validator_notes), 0.4), 3)


def citation_score(n_proposed: int, n_verified: int) -> float:
    """Fraction of proposed citations that survived verification. A case with
    no citations proposed at all is scored neutrally (0.5) rather than
    penalised — it simply had nothing to hallucinate."""
    if n_proposed <= 0:
        return 0.5
    return round(n_verified / n_proposed, 3)


def composite(
    *,
    ingestion_confidence: float,
    retrieval_coverage: float,
    validator_notes: list[str],
    n_proposed_citations: int,
    n_verified_citations: int,
    citation_engine: str = "scripted",
) -> dict:
    """Assemble the composite confidence score + a breakdown for the record."""
    breakdown = {
        "ingestion": round(ingestion_confidence, 3),
        "retrieval": round(retrieval_coverage, 3),
        "schema": schema_score(validator_notes),
        "citation": citation_score(n_proposed_citations, n_verified_citations),
    }
    final = round(sum(breakdown[k] * WEIGHTS[k] for k in WEIGHTS), 3)
    n_rejected = max(n_proposed_citations - n_verified_citations, 0)
    rejection_rate = round(n_rejected / n_proposed_citations, 3) if n_proposed_citations else 0.0

    return {
        "score": final,
        "weights": dict(WEIGHTS),
        "breakdown": breakdown,
        "citations_proposed": n_proposed_citations,
        "citations_verified": n_verified_citations,
        "citations_rejected": n_rejected,
        "citation_rejection_rate": rejection_rate,
        # Which path actually selected the citations for this case: "llm" means
        # the decoy-mixed selection step ran (a genuine test); "scripted" means
        # citations were sliced deterministically from retrieval (a structural
        # guarantee, but NOT evidence that any hallucination attempt was caught).
        "citation_engine": citation_engine,
        "decoys_tested": citation_engine == "llm",
    }
