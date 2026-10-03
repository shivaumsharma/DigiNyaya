"""Empirical retrieval quality (Precision/Recall/F1@K) for the Research
agent's precedent retrieval (app.rag.index.retrieve), measured against a
hand-authored ground-truth benchmark -- data_cache/rag_ground_truth_
benchmarks.json, 20 real queries each mapped to real precedent IDs that
actually exist in app/data/precedents.json (validated at load time below,
not just assumed).

WHY THIS DOESN'T ALSO RE-MEASURE CITATION HALLUCINATION: an earlier draft
of this task asked for that too, but scripts/measure_citation_grounding.py
already does exactly this (LLM-generated citations checked against
app.rag.verify_citations, both a grounded-vs-decoy test and a naive
no-retrieval baseline) -- rebuilding it here would just spend a second set
of live Sarvam calls to answer a question already answered. This script
reports that script's last saved result (data_cache/citation_grounding_
report.json) for context instead of re-running it; use
`python -m scripts.measure_citation_grounding` directly for a fresh one.

CORRECTED FROM THE ORIGINAL TASK SPEC (verified against the real code
before writing this, not assumed): retrieve()'s actual signature is
retrieve(query, signals, *, category, k, min_results) -- retrieval is
CATEGORY-SCOPED (one of the 4 registered dispute types, not the whole
corpus) and needs a `signals` list (the same app.agents.nlp.extract_signals
output the live Research agent passes -- see app/agents/research.py's own
call site, mirrored exactly here), not a bare query string with a generic
top_k. Every benchmark query therefore also carries its category, and
"relevant" precedent IDs are scoped to that category's slice of the corpus,
matching how retrieval actually works rather than measuring something the
live system never does.

Run (from backend/): python -m scripts.run_empirical_eval
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from app import rag  # noqa: E402
from app.agents import nlp  # noqa: E402
from app.data.loader import load_precedents  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
BENCHMARKS_PATH = _DATA_DIR / "rag_ground_truth_benchmarks.json"
CITATION_REPORT_PATH = _DATA_DIR / "citation_grounding_report.json"
OUT_PATH = _DATA_DIR / "rag_eval_results.json"

K_VALUES = (3, 5)


def _load_benchmarks() -> list[dict]:
    benchmarks = json.loads(BENCHMARKS_PATH.read_text(encoding="utf-8"))
    precedent_ids = {p["id"] for p in load_precedents()}
    # Fail loudly on a stale/typo'd ground-truth ID rather than silently
    # under-counting recall for whatever query it belongs to -- a benchmark
    # that quietly stops meaning what it claims to is worse than no
    # benchmark at all.
    for b in benchmarks:
        missing = [pid for pid in b["relevant_precedent_ids"] if pid not in precedent_ids]
        if missing:
            raise ValueError(
                f"Ground-truth query {b['query_id']!r} references precedent ID(s) not found in "
                f"app/data/precedents.json: {missing}. Fix the benchmark file or the corpus."
            )
    return benchmarks


def _eval_one(query: dict, k: int) -> dict:
    signals = nlp.extract_signals(query["query"])
    t0 = time.perf_counter()
    result = rag.retrieve(query["query"], signals, category=query["category"], k=k)
    latency_ms = (time.perf_counter() - t0) * 1000

    retrieved_ids = [p["id"] for p in result["precedents"]]
    relevant_ids = set(query["relevant_precedent_ids"])
    retrieved_set = set(retrieved_ids)

    tp = len(retrieved_set & relevant_ids)
    fp = len(retrieved_set - relevant_ids)
    fn = len(relevant_ids - retrieved_set)
    precision = tp / len(retrieved_set) if retrieved_set else 0.0
    recall = tp / len(relevant_ids) if relevant_ids else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "query_id": query["query_id"],
        "category": query["category"],
        "k_requested": k,
        "k_actual_returned": len(retrieved_ids),
        "retrieval_method": result["method"],
        "retrieved_ids": retrieved_ids,
        "relevant_ids": sorted(relevant_ids),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "latency_ms": round(latency_ms, 2),
    }


def _aggregate(per_query: list[dict], k: int) -> dict:
    rows = [r for r in per_query if r["k_requested"] == k]
    n = len(rows)
    # Macro-average (mean of each query's own P/R/F1) is the headline number
    # -- it weighs every query equally regardless of how many relevant docs
    # it has, which is the right behaviour for "how good is retrieval on a
    # representative query", not "how good is it on whichever query happens
    # to have the most relevant documents". Micro (pooled TP/FP/FN across
    # all queries first, then one P/R/F1) is reported alongside since the
    # two really can diverge and hiding either would be presenting a
    # single, possibly flattering, framing as the whole truth.
    macro_p = round(sum(r["precision"] for r in rows) / n, 3) if n else None
    macro_r = round(sum(r["recall"] for r in rows) / n, 3) if n else None
    macro_f1 = round(sum(r["f1"] for r in rows) / n, 3) if n else None

    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    micro_p = round(tp / (tp + fp), 3) if (tp + fp) else 0.0
    micro_r = round(tp / (tp + fn), 3) if (tp + fn) else 0.0
    micro_f1 = round(2 * micro_p * micro_r / (micro_p + micro_r), 3) if (micro_p + micro_r) else 0.0

    avg_latency_ms = round(sum(r["latency_ms"] for r in rows) / n, 2) if n else None
    semantic_count = sum(1 for r in rows if r["retrieval_method"] == "semantic")

    return {
        "k": k,
        "n_queries": n,
        "macro_precision": macro_p, "macro_recall": macro_r, "macro_f1": macro_f1,
        "micro_precision": micro_p, "micro_recall": micro_r, "micro_f1": micro_f1,
        "avg_latency_ms": avg_latency_ms,
        "retrieval_method_used": (
            "semantic" if semantic_count == n else "keyword" if semantic_count == 0
            else f"mixed ({semantic_count}/{n} semantic -- embedding service may be intermittently unavailable)"
        ),
    }


def main() -> int:
    benchmarks = _load_benchmarks()
    print(f"Loaded {len(benchmarks)} ground-truth queries, all IDs verified against the real corpus.\n")

    per_query = [_eval_one(q, k) for q in benchmarks for k in K_VALUES]
    aggregates = {k: _aggregate(per_query, k) for k in K_VALUES}

    print(f"{'K':<4}{'Precision':<12}{'Recall':<10}{'F1':<8}{'(macro)':<10}   "
          f"{'Precision':<12}{'Recall':<10}{'F1':<8}{'(micro)':<10}{'Avg latency':<14}Method")
    for k in K_VALUES:
        a = aggregates[k]
        print(
            f"{k:<4}{a['macro_precision']:<12}{a['macro_recall']:<10}{a['macro_f1']:<8}{'':<10}   "
            f"{a['micro_precision']:<12}{a['micro_recall']:<10}{a['micro_f1']:<8}{'':<10}"
            f"{a['avg_latency_ms']}ms{'':<8}{a['retrieval_method_used']}"
        )

    print("\nPer-query detail:")
    for k in K_VALUES:
        print(f"\n  --- k={k} ---")
        for r in per_query:
            if r["k_requested"] != k:
                continue
            print(f"    {r['query_id']} ({r['category']}): P={r['precision']} R={r['recall']} "
                  f"F1={r['f1']}  retrieved={r['retrieved_ids']}  relevant={r['relevant_ids']}")

    citation_context = None
    if CITATION_REPORT_PATH.exists():
        prior = json.loads(CITATION_REPORT_PATH.read_text(encoding="utf-8"))
        citation_context = {
            k: v for k, v in prior.items()
            if k in ("n_sampled", "grounded_hallucination_rate", "naive_unverifiable_rate")
        }
        print(
            f"\nCitation hallucination context (from the last "
            f"`python -m scripts.measure_citation_grounding` run, not re-run here -- see module "
            f"docstring for why): grounded={citation_context.get('grounded_hallucination_rate')}  "
            f"naive_unverifiable={citation_context.get('naive_unverifiable_rate')}  "
            f"(n={citation_context.get('n_sampled')})"
        )
    else:
        print(
            "\nNo prior citation_grounding_report.json found -- run "
            "`python -m scripts.measure_citation_grounding` separately for that number."
        )

    report = {
        "n_benchmark_queries": len(benchmarks),
        "k_values": list(K_VALUES),
        "aggregates": aggregates,
        "per_query": per_query,
        "citation_hallucination_context": citation_context,
    }
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote full report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
