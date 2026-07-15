"""Precedent retrieval.

Primary path: real semantic search — embed the corpus once (via Ollama's
nomic-embed-text) and rank by cosine similarity to the case query. If the embed
model isn't present, transparently fall back to keyword/tag overlap. Either way
we return a relevance score, a coverage estimate, and the method used (so the UI
can be honest about it).

Pure-Python cosine keeps this dependency-free for a small corpus.
"""

from __future__ import annotations

import math
import threading

from .. import llm
from ..data.loader import load_precedents

_CURRENT_YEAR = 2024
_lock = threading.Lock()
_doc_vectors: list[list[float]] | None = None
_doc_built = False


def _doc_text(p: dict) -> str:
    return f"{p['title']}. {p['summary']} {p['principle']} Tags: {', '.join(p.get('tags', []))}."


def _ensure_embeddings() -> bool:
    """Embed the corpus once. Returns True if semantic search is available."""
    global _doc_vectors, _doc_built
    with _lock:
        if _doc_built:
            return _doc_vectors is not None
        _doc_built = True
        precedents = load_precedents()
        try:
            vecs = llm.embed([_doc_text(p) for p in precedents])
        except Exception:
            # Ollama was reachable (is_available() passed) but the actual
            # embed call failed anyway -- e.g. the embedding model isn't
            # pulled, returning a 500. Don't let that crash the agent;
            # fall back to keyword search just like the "unreachable" case.
            vecs = None
        _doc_vectors = vecs
        return _doc_vectors is not None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _recency(year: int) -> float:
    age = max(_CURRENT_YEAR - year, 0)
    return 1.0 / (1.0 + 0.12 * age)


def _keyword_score(p: dict, signals: set[str]) -> float:
    tags = set(p.get("tags", []))
    overlap = tags & signals
    base = (len(overlap) / max(len(signals), 1)) if overlap else 0.05
    return (0.8 * base + 0.2) * _recency(p.get("year", _CURRENT_YEAR))


def retrieve(query: str, signals: list[str], *, k: int = 5, min_results: int = 3) -> dict:
    """Return ranked precedents + coverage + method."""
    precedents = load_precedents()
    consumer = [p for p in precedents if p.get("category") == "consumer_dispute"]
    sigset = set(signals)

    method = "keyword"
    scored: list[tuple[float, dict]] = []

    if _ensure_embeddings() and _doc_vectors is not None:
        try:
            qvec = llm.embed([query])
        except Exception:
            qvec = None
        if qvec:
            method = "semantic"
            qv = qvec[0]
            # Map corpus index -> vector (consumer subset preserves order).
            idx_by_id = {p["id"]: i for i, p in enumerate(precedents)}
            for p in consumer:
                sim = _cosine(qv, _doc_vectors[idx_by_id[p["id"]]])
                # Blend semantic similarity with a mild recency prior.
                scored.append((sim * 0.85 + _recency(p["year"]) * 0.15, p))

    if not scored:  # keyword fallback
        method = "keyword"
        for p in consumer:
            scored.append((_keyword_score(p, sigset), p))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]
    top_score = top[0][0] if top else 1.0

    relevant = [(s, p) for s, p in top if s > (0.35 if method == "semantic" else 0.18)]
    if len(relevant) < min_results:
        relevant = top[:min_results]

    results = []
    for raw, p in relevant:
        results.append(
            {
                "id": p["id"],
                "title": p["title"],
                "court": p["court"],
                "year": p["year"],
                "citation": p["citation"],
                "summary": p["summary"],
                "principle": p["principle"],
                "outcome": p["outcome"],
                "outcome_detail": p["outcome_detail"],
                "relief_amount_ratio": p["relief_amount_ratio"],
                "compliance_days": p["compliance_days"],
                "relevance": round(min(raw / (top_score or 1), 1.0) * 100, 1),
                "matched_signals": sorted(set(p.get("tags", [])) & sigset),
            }
        )

    coverage_score = round(min(top_score, 1.0), 3)
    return {
        "precedents": results,
        "corpus_size": len(precedents),
        "coverage_score": coverage_score,
        "coverage_label": coverage_label(coverage_score, method),
        "method": method,
    }


def coverage_label(score: float, method: str) -> str:
    hi, mid = (0.6, 0.45) if method == "semantic" else (0.5, 0.3)
    if score >= hi:
        return "strong"
    if score >= mid:
        return "moderate"
    return "thin"


def verify_citations(cited_ids: list[str], retrieved_ids: list[str]) -> list[str]:
    """Return only citations that were actually retrieved (drops hallucinated refs)."""
    allowed = set(retrieved_ids)
    return [c for c in cited_ids if c in allowed]


def decoy_candidates(retrieved_ids: list[str], *, k: int = 2) -> list[dict]:
    """Sample consumer-dispute precedents that were NOT retrieved for this case.

    Used to build a mixed candidate pool (real matches + decoys) so the
    Resolution agent's citation-selection step is a genuine test rather than a
    tautology: if it's fed only what was retrieved, verification against that
    same set can never reject anything.
    """
    import random

    precedents = load_precedents()
    pool = [p for p in precedents if p.get("category") == "consumer_dispute" and p["id"] not in set(retrieved_ids)]
    random.shuffle(pool)
    return pool[:k]