"""Agent 2 — Precedent Research Agent.

Performs real semantic retrieval (with keyword fallback) over the corpus of
Indian consumer-court judgments. On a research *re-run* (triggered by the
Analysis agent when coverage is thin) it broadens the query.
"""

from __future__ import annotations

from .. import rag
from ..core.context import CaseContext, ResearchResult, RetrievedPrecedent
from . import nlp
from .base import AgentResult


def run(ctx: CaseContext) -> AgentResult:
    ing = ctx.ingestion
    signals = list(ing.signals) if ing else []
    label = nlp.dispute_label(ctx.dispute_type)

    # Build the retrieval query. On a retry, broaden it with the subtype words
    # and drop the most specific tokens to widen recall.
    if ctx.research_retries == 0:
        query = f"{ing.dispute_subtype}. {ctx.description}" if ing else ctx.description
        k = 5
    else:
        subtype = ing.dispute_subtype if ing else label
        query = f"{label} {subtype} {' '.join(signals)}"
        k = 7

    res = rag.retrieve(query, signals, category=ctx.dispute_type, k=k)

    precedents = [RetrievedPrecedent(**p) for p in res["precedents"]]
    result = ResearchResult(
        precedents=precedents,
        corpus_size=res["corpus_size"],
        coverage_score=res["coverage_score"],
        coverage_label=res["coverage_label"],
        method=res["method"],
        query=query,
    )

    method_label = "semantic vector search" if res["method"] == "semantic" else "keyword matching"
    retry_note = " (broadened re-search)" if ctx.research_retries else ""
    detail = (
        f"Searched {res['corpus_size']} judgments via {method_label}{retry_note}. "
        f"Retrieved {len(precedents)} precedents · coverage: {res['coverage_label']}. "
        + (f"Top match: {precedents[0].title} ({precedents[0].relevance}%)." if precedents else "")
    )
    return AgentResult(
        output=result,
        detail=detail,
        confidence=res["coverage_score"],
        citations=[p.id for p in precedents],
        engine=res["method"],
    )
