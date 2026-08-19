"""Run the dedicated cheque_bounce eval (built by
scripts/build_cheque_bounce_eval.py) through the real pipeline and judge it
against the real Section 138 NI Act outcomes -- the eval this dispute type
has never had, per app/agents/ingestion.py's own comment gating it out of
Tier-1 autonomy "until precedent coverage and eval results justify
promoting."

Kept entirely separate from the main civil eval/judge scripts: same
underlying machinery (extract_case_signals.extract_signals(),
judge_real_outcomes.run_and_capture()/judge()), different, much smaller
dataset, own output file.

Run (from backend/): python -m scripts.run_cheque_bounce_eval
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from scripts.extract_case_signals import extract_signals  # noqa: E402
from scripts.judge_real_outcomes import judge, run_and_capture  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
DATASET_PATH = _DATA_DIR / "eval_judgments_cheque_bounce.json"
OUT_PATH = _DATA_DIR / "cheque_bounce_verdict_comparison.json"


def main() -> int:
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not llm.is_available():
        print("ERROR: LLM unavailable.")
        return 1

    # Signals (evidence count, defense grounds) matter a lot per this
    # session's whole investigation -- extract them if missing, same as the
    # main eval's incremental convention.
    changed = False
    for case in cases:
        if case.get("signals"):
            continue
        print(f"[signals] {case['case_id']}...", end=" ", flush=True)
        signals = extract_signals(case)
        case["signals"] = signals
        changed = True
        print("ok" if signals else "FAILED (placeholder fallback)")
    if changed:
        DATASET_PATH.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")

    results = []
    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] {case['case_id']}...", end=" ", flush=True)
        ai = run_and_capture(case, live_llm=True)
        if ai is None:
            print("skipped (escalated -- no AI resolution to compare)")
            continue
        verdict = judge(case, ai)
        if not verdict or verdict.get("verdict") not in ("match", "partial", "mismatch"):
            print("JUDGE FAILED")
            continue
        print(f"{verdict['verdict']} -- {verdict.get('reason', '')[:90]}")
        results.append({
            "case_id": case["case_id"],
            "verdict": verdict["verdict"],
            "reason": verdict.get("reason"),
            "ai_relief": ai["relief_amount_display"],
            "ai_relief_type": ai["relief_type"],
            "real_outcome": case["expected_outcome"][:200],
        })

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    counts = {"match": 0, "partial": 0, "mismatch": 0}
    for r in results:
        counts[r["verdict"]] += 1
    print("\n" + "=" * 60)
    print(f"cheque_bounce eval: {len(results)}/{len(cases)} judged -> {OUT_PATH}")
    print(f"  MATCH: {counts['match']}  PARTIAL: {counts['partial']}  MISMATCH: {counts['mismatch']}")
    if results:
        print(f"  Exact match: {counts['match']}/{len(results)} ({round(100*counts['match']/len(results))}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
