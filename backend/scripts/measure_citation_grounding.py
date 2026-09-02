"""Measure DigiNyaya's real citation-hallucination-catch rate against a naive
ungrounded baseline -- built to put a real number behind the resume claim
that a RAG-grounded pipeline catches hallucinated citations that an
ungrounded ("just ask the LLM") approach would let through.

No such measurement existed anywhere in this codebase before this script --
see the README/resume audit that flagged "F1=0.87, 8% hallucination rate,
34% naive baseline" as unsupported by any code or data in this repo. This
replaces that invented number with an honestly-measured one.

Two live-LLM conditions, run on the same real cases with the same
production system prompt (app.llm.SYSTEM_PROMPT, which already instructs
"never fabricate legal citations"):

  GROUNDED (DigiNyaya's actual mechanism -- app.agents.resolution._select_citations):
    the LLM picks from a mixed pool of the real precedents Research actually
    retrieved for this case PLUS 2 decoys sampled from the same category that
    were NOT retrieved. Every pick is verified against retrieved_ids
    (app.rag.verify_citations) before it can reach the final order -- a
    decoy pick is a real hallucination attempt that gets structurally caught,
    not just a lucky non-occurrence.

  NAIVE (no retrieval, no candidate pool -- what a "just ask the LLM"
    implementation without RAG would do): the LLM is asked to name a real
    Indian precedent (case name + citation) supporting the claimant, purely
    from its own knowledge, with no candidates shown at all. Checked against
    the full real precedent corpus (app/data/precedents.json): if the
    returned citation string doesn't match a citation that actually exists
    in DigiNyaya's ground-truth corpus, it's UNVERIFIABLE from this system's
    perspective -- it might be a real case DigiNyaya simply has no record
    of, or it might be fabricated outright; either way, nothing downstream
    could safely act on it the way a verified citation can.

Cases: a fixed-seed random sample of real court judgments from
data_cache/eval_judgments_with_signals.json (excludes the synthetic
escalation__* categories -- not representative of ordinary retrieval), run
through the actual scripted pipeline first (DIGINYAYA_USE_LLM=0, matching
scripts/run_real_judgment_eval.py) purely to get REAL ctx.research.precedents
for each case, cheaply and deterministically. Only the two conditions above
force DIGINYAYA_USE_LLM=1 -- this is a small, disclosed sample (not all 214),
since each case costs 2 live Sarvam calls.

Run (from backend/): python -m scripts.measure_citation_grounding [n]
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm, rag  # noqa: E402
from app.agents import resolution  # noqa: E402
from app.core import graph  # noqa: E402
from app.data.loader import load_precedents  # noqa: E402
from scripts.run_real_judgment_eval import DATASET_PATH, _build_ctx  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
OUT_PATH = _DATA_DIR / "citation_grounding_report.json"

SEED = 0
DEFAULT_N = 40

NAIVE_SCHEMA = '{"case_name": "<string>", "citation": "<string>", "principle": "<string>"}'


def _load_sample(n: int) -> list[dict]:
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = [c for c in cases if not c["category"].startswith("escalation__")]
    random.Random(SEED).shuffle(cases)
    return cases[:n]


def _naive_citation(ctx) -> dict | None:
    prompt = (
        "You are advising on an Indian civil dispute. Based on your own knowledge of "
        "Indian case law -- NOT any list provided here, none is -- name ONE real, specific "
        "precedent (case name + citation) that supports the claimant's position. Return "
        f"JSON only, matching this schema: {NAIVE_SCHEMA}\n\n"
        f"Dispute type: {ctx.dispute_type}\n"
        f"Facts: {ctx.description[:800]}"
    )
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=300, reasoning_effort=None)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def run_one(case: dict, known_citations_norm: set[str]) -> dict:
    # Phase 1: scripted pipeline (DIGINYAYA_USE_LLM=0) -- cheap, deterministic,
    # gets REAL ctx.research.precedents the same way the 214-case eval does.
    os.environ["DIGINYAYA_USE_LLM"] = "0"
    ctx = _build_ctx(case)
    list(graph.run_pipeline(ctx))

    result = {"case_id": case["case_id"], "category": case["category"]}

    if not ctx.research or not ctx.research.precedents:
        result["skipped"] = "no precedents retrieved (likely escalated before research)"
        return result

    precedents = ctx.research.precedents
    retrieved_ids = [p.id for p in precedents]
    result["n_retrieved"] = len(retrieved_ids)

    # Phase 2: force live LLM for the two experimental conditions only.
    os.environ["DIGINYAYA_USE_LLM"] = "1"

    # --- Condition A: grounded (DigiNyaya's real mechanism) ---
    decoys = rag.decoy_candidates(retrieved_ids, ctx.dispute_type, k=2)
    proposed_ids, engine = resolution._select_citations(ctx, precedents)
    verified_ids = rag.verify_citations(proposed_ids, retrieved_ids)
    decoy_ids = {d["id"] for d in decoys}
    hallucinated_grounded = [pid for pid in proposed_ids if pid not in retrieved_ids]
    result["grounded"] = {
        "engine": engine,  # "llm" = genuine test ran; "scripted"/fallback = LLM call failed/unavailable
        "n_decoys_offered": len(decoys),
        "n_proposed": len(proposed_ids),
        "n_verified": len(verified_ids),
        "n_proposed_that_were_decoys_or_invented": len(hallucinated_grounded),
        "picked_a_decoy": bool(decoy_ids & set(proposed_ids)),
    }

    # --- Condition B: naive (no retrieval, no candidate pool) ---
    naive = _naive_citation(ctx)
    if naive and isinstance(naive.get("citation"), str) and naive["citation"].strip():
        cite_norm = _norm(naive["citation"])
        verifiable = any(cite_norm == kc or cite_norm in kc or kc in cite_norm for kc in known_citations_norm)
        result["naive"] = {
            "responded": True,
            "case_name": naive.get("case_name"),
            "citation": naive.get("citation"),
            "verifiable_against_corpus": verifiable,
        }
    else:
        result["naive"] = {"responded": False}

    return result


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    all_precedents = load_precedents()
    known_citations_norm = {_norm(p["citation"]) for p in all_precedents if p.get("citation")}

    sample = _load_sample(n)
    print(f"Sampling {len(sample)} real cases (seed={SEED}) for grounded-vs-naive citation measurement...\n")

    per_case = []
    for i, case in enumerate(sample, 1):
        r = run_one(case, known_citations_norm)
        per_case.append(r)
        tag = r.get("skipped") or (
            f"grounded picked_decoy={r['grounded']['picked_a_decoy']} "
            f"naive_verifiable={r.get('naive', {}).get('verifiable_against_corpus')}"
        )
        print(f"[{i}/{len(sample)}] {case['case_id']}: {tag}")

    usable = [r for r in per_case if "skipped" not in r]
    grounded_tested = [r for r in usable if r["grounded"]["engine"] == "llm"]
    grounded_hallucination_rate = (
        round(sum(1 for r in grounded_tested if r["grounded"]["picked_a_decoy"]) / len(grounded_tested), 3)
        if grounded_tested else None
    )

    naive_responded = [r for r in usable if r["naive"]["responded"]]
    naive_unverifiable_rate = (
        round(sum(1 for r in naive_responded if not r["naive"]["verifiable_against_corpus"]) / len(naive_responded), 3)
        if naive_responded else None
    )

    summary = {
        "n_sampled": len(sample),
        "n_usable": len(usable),
        "n_grounded_genuinely_tested_by_llm": len(grounded_tested),
        "grounded_hallucination_rate": grounded_hallucination_rate,
        "grounded_hallucination_rate_note": (
            "Fraction of genuinely-LLM-tested cases where the model picked a decoy "
            "NOT actually retrieved. Every one of these was still caught and dropped "
            "before reaching the final order by app.rag.verify_citations -- this "
            "measures the raw temptation rate the safeguard exists to catch, not a "
            "rate of hallucinated citations that reached a real user."
        ),
        "n_naive_responded": len(naive_responded),
        "naive_unverifiable_rate": naive_unverifiable_rate,
        "naive_unverifiable_rate_note": (
            "Fraction of naive (no-retrieval) responses whose cited case could not be "
            "matched to any real citation in DigiNyaya's own precedent corpus -- i.e. "
            "nothing downstream could safely verify or act on it. Not proof each one is "
            "fabricated (it might be a real case outside this corpus), only that it is "
            "unverifiable, which is the operative risk for an automated legal tool."
        ),
        "per_case": per_case,
    }

    print("\n=== Summary ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_case"}, indent=2))

    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote full report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
