"""Precedent retrieval.

Primary path: real semantic search — embed the corpus once (via Ollama's
nomic-embed-text) and rank by cosine similarity to the case query. If the embed
model isn't present, transparently fall back to keyword/tag overlap. Either way
we return a relevance score, a coverage estimate, and the method used (so the UI
can be honest about it).

Pure-Python cosine keeps this dependency-free for a small corpus.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path

from .. import llm
from ..data.loader import load_precedents

_CURRENT_YEAR = 2024
_lock = threading.Lock()
_doc_vectors: list[list[float]] | None = None
_doc_built = False

# Embedding the corpus is 1 Ollama HTTP call PER precedent (llm.embed() has
# no batch endpoint to call -- see app/llm/client.py), so re-embedding all
# 127+ precedents from scratch on every single process restart is a real,
# user-visible cold-start cost on the first case that reaches Agent 2
# (confirmed: this is why "Agent 2" looks slow right after starting the
# server). Cached to disk, keyed by a hash of the corpus content, so a
# restart only re-embeds when the precedent corpus actually changed.
_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "precedent_vectors_cache.json"


def _doc_text(p: dict) -> str:
    return f"{p['title']}. {p['summary']} {p['principle']} Tags: {', '.join(p.get('tags', []))}."


def _corpus_hash(precedents: list[dict]) -> str:
    texts = "\x00".join(f"{p['id']}\x01{_doc_text(p)}" for p in precedents)
    return hashlib.sha256(texts.encode("utf-8")).hexdigest()


def _load_cached_vectors(expected_hash: str, expected_len: int) -> list[list[float]] | None:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if cached.get("hash") != expected_hash:
        return None
    vecs = cached.get("vectors")
    if not isinstance(vecs, list) or len(vecs) != expected_len:
        return None
    return vecs


def _save_cached_vectors(corpus_hash: str, vecs: list[list[float]]) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"hash": corpus_hash, "vectors": vecs}, fh)
    except OSError:
        pass  # Cache is a pure optimization -- a write failure just means next boot re-embeds.


def _ensure_embeddings() -> bool:
    """Embed the corpus once (disk-cached across restarts). Returns True if
    semantic search is available."""
    global _doc_vectors, _doc_built
    with _lock:
        if _doc_built:
            return _doc_vectors is not None
        _doc_built = True
        precedents = load_precedents()
        corpus_hash = _corpus_hash(precedents)

        vecs = _load_cached_vectors(corpus_hash, len(precedents))
        if vecs is None:
            try:
                vecs = llm.embed([_doc_text(p) for p in precedents])
            except Exception:
                # Ollama was reachable (is_available() passed) but the actual
                # embed call failed anyway -- e.g. the embedding model isn't
                # pulled, returning a 500. Don't let that crash the agent;
                # fall back to keyword search just like the "unreachable" case.
                vecs = None
            if vecs is not None:
                _save_cached_vectors(corpus_hash, vecs)

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


def retrieve(
    query: str,
    signals: list[str],
    *,
    category: str = "consumer_dispute",
    k: int = 5,
    min_results: int = 3,
) -> dict:
    """Return ranked precedents + coverage + method, scoped to *category*."""
    precedents = load_precedents()
    in_category = [p for p in precedents if p.get("category") == category]
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
            # Map corpus index -> vector (full corpus preserves order).
            idx_by_id = {p["id"]: i for i, p in enumerate(precedents)}
            for p in in_category:
                sim = _cosine(qv, _doc_vectors[idx_by_id[p["id"]]])
                # Blend semantic similarity with a mild recency prior.
                scored.append((sim * 0.85 + _recency(p["year"]) * 0.15, p))

    if not scored:  # keyword fallback
        method = "keyword"
        for p in in_category:
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


def decoy_candidates(retrieved_ids: list[str], category: str = "consumer_dispute", *, k: int = 2) -> list[dict]:
    """Sample same-category precedents that were NOT retrieved for this case.

    Used to build a mixed candidate pool (real matches + decoys) so the
    Resolution agent's citation-selection step is a genuine test rather than a
    tautology: if it's fed only what was retrieved, verification against that
    same set can never reject anything.
    """
    import random

    precedents = load_precedents()
    pool = [p for p in precedents if p.get("category") == category and p["id"] not in set(retrieved_ids)]
    random.shuffle(pool)
    return pool[:k]