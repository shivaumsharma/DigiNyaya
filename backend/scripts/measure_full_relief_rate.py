"""Compute the real "full-relief rate" for Tier 1 mediated cases -- the other
number in the homepage stat block (frontend/src/i18n/en.json's statTiles,
"82%, Full-relief rate, Tier 1 mediated cases") that turned out to have no
real measurement behind it, same as the 78%/100+ escalation figure fixed
alongside this script (see scripts/measure_escalation_rate.py).

Re-runs the full pipeline (scripted mode) through the resolution stage for
every real, sourced case, and for each Tier-1 resolution compares the
relief actually awarded to the claim amount: "full relief" means the
resolution's relief_amount is (near-)exactly the claimed amount, not a
partial/reduced award or a dismissal.

Run (from backend/): python -m scripts.measure_full_relief_rate
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from scripts.run_real_judgment_eval import _build_ctx, DATASET_PATH  # noqa: E402
from app.core import graph  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "full_relief_rate_report.json"

# A resolution within 2% of the claimed amount counts as "full relief" --
# tolerance for rounding in interest/fee calculations, not a fudge on the
# substantive question of whether the claimant got what they asked for.
FULL_RELIEF_TOLERANCE = 0.02


def main() -> int:
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    print(f"Running {len(cases)} case(s) through resolution to measure full-relief rate...")

    tier1_results = []
    for i, case in enumerate(cases):
        if (i + 1) % 200 == 0:
            print(f"  [{i + 1}/{len(cases)}]...")
        ctx = _build_ctx(case)
        try:
            list(graph.run_pipeline(ctx))
            if ctx.escalation is None and ctx.mediation is not None:
                list(graph.run_resolution(ctx, via_mediation=True))
        except Exception:
            continue
        if ctx.escalation is not None or ctx.resolution is None:
            continue
        if ctx.tier != 1:
            continue
        claim_amount = ctx.claim_amount
        relief_amount = ctx.resolution.relief_amount
        if not claim_amount or claim_amount <= 0:
            continue
        ratio = relief_amount / claim_amount
        tier1_results.append({
            "case_id": case["case_id"],
            "claim_amount": claim_amount,
            "relief_amount": relief_amount,
            "ratio": ratio,
            "full_relief": ratio >= (1 - FULL_RELIEF_TOLERANCE),
        })

    n = len(tier1_results)
    full = sum(1 for r in tier1_results if r["full_relief"])
    report = {
        "n_tier1_resolved_with_claim_amount": n,
        "n_full_relief": full,
        "full_relief_rate": round(full / n, 3) if n else None,
        "mean_ratio": round(sum(r["ratio"] for r in tier1_results) / n, 3) if n else None,
    }
    print(json.dumps(report, indent=2))
    OUT_PATH.write_text(json.dumps({"report": report, "cases": tier1_results}, indent=2), encoding="utf-8")
    print(f"\nWrote report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
