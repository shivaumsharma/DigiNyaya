"""Baseline comparison: a PLAIN LLM call given the exact same case facts
DigiNyaya's pipeline has (claimant's description, claim amount, respondent's
defense if any) -- but none of DigiNyaya's machinery: no RAG/precedent
retrieval, no structured evidence scoring, no mediation formula, no
citation verification. One holistic decision, same as asking a bare LLM
"who wins and what should happen" with zero engineering around it.

WHY THIS EXISTS: every number reported all session has been DigiNyaya
compared to an earlier version of DigiNyaya. That proves we fixed our own
regressions, not that the architecture itself earns its complexity over a
naive baseline. This script is the missing control.

Fairness: builds the respondent statement the exact same way
scripts/run_real_judgment_eval.py's _build_ctx() does, so the baseline sees
identical input facts -- only the "how a decision gets made" step differs.

Reuses judge_real_outcomes.judge() (the same, now-fixed two-boolean judge)
so this baseline's accuracy is directly comparable to DigiNyaya's own,
graded by the identical instrument.

Run (from backend/):
  python -m scripts.run_baseline_llm_eval --sample 100   # quick POC first
  python -m scripts.run_baseline_llm_eval                # full corpus
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from scripts.run_real_judgment_eval import _build_ctx  # noqa: E402
from scripts.judge_real_outcomes import judge  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
SIGNALS_PATH = _DATA_DIR / "eval_judgments_with_signals.json"
VERDICTS_PATH = _DATA_DIR / "real_judgment_verdict_comparison.json"
OUT_PATH = _DATA_DIR / "baseline_llm_verdict_comparison.json"

RANDOM_SEED = 42


def _decide(case: dict) -> dict | None:
    ctx = _build_ctx(case)
    respondent_line = (
        f"Respondent's defense: {ctx.respondent_submission['statement']}"
        if ctx.respondent_submission else "Respondent did not respond."
    )
    schema = (
        '{"claimant_prevails": <true|false: should the claimant win real relief>, '
        '"relief_type": "monetary|non_monetary|dismissed", '
        '"relief_amount": <number, the amount awarded if monetary, else null>, '
        '"reasoning": "<=40 words"}'
    )
    prompt = (
        f"You are deciding a {case['category'].replace('_', ' ')} dispute. Decide who should win and "
        f"what relief (if any). Return JSON only, matching this schema: {schema}\n\n"
        f"Claim amount: {ctx.claim_amount}\n"
        f"Claimant's case: {ctx.description}\n"
        f"{respondent_line}\n"
    )
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=600, temperature=0.0)


def _to_ai_dict(decision: dict) -> dict:
    relief_type = decision.get("relief_type") or "dismissed"
    amount = decision.get("relief_amount")
    amount = float(amount) if isinstance(amount, (int, float)) else 0.0
    return {
        "relief_type": "compensation" if relief_type == "monetary" else relief_type,
        "relief_amount_display": f"Rs. {amount:,.2f}",
        "order": [decision.get("reasoning") or ""],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Baseline: plain LLM, no DigiNyaya pipeline, same case facts.")
    ap.add_argument("--sample", type=int, default=None, help="if set, run only this many cases (stratified)")
    ap.add_argument("--category", default=None, help="restrict to one category")
    args = ap.parse_args(argv)

    if not llm.is_available():
        print("ERROR: LLM unavailable.")
        return 1

    signals = {s["case_id"]: s for s in json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))}
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    universe = [v for v in verdicts if v["case_id"] in signals]
    if args.category:
        universe = [v for v in universe if v["category"] == args.category]

    if args.sample:
        by_cat: dict[str, list] = {}
        for v in universe:
            by_cat.setdefault(v["category"], []).append(v)
        rng = random.Random(RANDOM_SEED)
        total = len(universe)
        chosen = []
        for cat, rows in by_cat.items():
            k = min(len(rows), max(min(10, len(rows)), round(args.sample * len(rows) / total)))
            chosen.extend(rng.sample(rows, k))
        universe = chosen[: args.sample * 2]  # generous cap, category floors can overshoot slightly

    print(f"Running baseline LLM on {len(universe)} case(s) (no DigiNyaya pipeline, same input facts)...")

    cached: dict[str, dict] = {}
    if OUT_PATH.exists():
        cached = {r["case_id"]: r for r in json.loads(OUT_PATH.read_text(encoding="utf-8"))}

    results = list(cached.values())
    consecutive_failures = 0
    to_run = [v for v in universe if v["case_id"] not in cached]
    print(f"{len(to_run)} case(s) need a fresh baseline decision + judge call.")

    for i, v in enumerate(to_run):
        cid = v["case_id"]
        case = signals[cid]
        decision = _decide(case)
        if decision is None or "claimant_prevails" not in decision:
            consecutive_failures += 1
            if consecutive_failures >= 2:
                time.sleep(32)
                decision = _decide(case)
            if decision is None or "claimant_prevails" not in decision:
                print(f"  [{i+1}/{len(to_run)}] {cid}: DECISION FAILED")
                continue
        consecutive_failures = 0

        ai = _to_ai_dict(decision)
        verdict = judge(case, ai)
        if verdict is None:
            print(f"  [{i+1}/{len(to_run)}] {cid}: JUDGE FAILED")
            continue

        results.append({
            "case_id": cid,
            "category": v["category"],
            "baseline_decision": decision,
            "verdict": verdict.get("verdict"),
            "reason": verdict.get("reason"),
        })
        time.sleep(0.4)
        if (i + 1) % 25 == 0 or i + 1 == len(to_run):
            print(f"  [{i+1}/{len(to_run)}] done")
            OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    print(f"\nTotal: {len(results)} case(s)")
    print(Counter(r["verdict"] for r in results))
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["verdict"])
    for cat, vs in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        n = len(vs)
        m = sum(1 for x in vs if x == "match")
        print(f"  {cat}: {m}/{n} = {m/n:.1%}")
    print(f"\nWrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
