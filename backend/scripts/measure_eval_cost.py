"""Token-cost regression gate for the golden eval suite (CI Stage 4).

Runs the same 18 golden cases as scripts/eval_cases.py, but with real LLM
calls (DIGINYAYA_USE_LLM=1) against Sarvam, and measures actual token usage
per case via app.llm.get_usage_totals()/reset_usage_totals() -- there was no
token-tracking anywhere in the codebase before this script; it's new
instrumentation (app/llm/base.py, app/llm/providers/sarvam.py,
app/llm/client.py), not something that already existed.

This is DELIBERATELY NOT wired into the always-on ci.yml pull_request/push
triggers: unlike scripts/eval_cases.py (which runs scripted, DIGINYAYA_USE_LLM=0,
free, no API key needed), this makes ~4 real Sarvam calls per case x 18 cases
= real, billed API usage every time it runs. See .github/workflows/cost-gate.yml
for how it's actually triggered (workflow_dispatch by default -- opt-in, not
automatic on every push).

Gates on TOKEN count, not a dollar figure -- Sarvam's per-token pricing isn't
hardcoded here (it changes, and guessing it would be exactly the kind of
unverified number this project has been careful to avoid elsewhere). Tokens
are the real, measurable signal; converting to $ is left to whoever reads the
output, using Sarvam's current published rate.

Usage (from the backend folder, with a real SARVAM_API_KEY in the environment):
    python -m scripts.measure_eval_cost              # compare against the committed baseline
    python -m scripts.measure_eval_cost --write-baseline   # overwrite the baseline with this run
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["DIGINYAYA_USE_LLM"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm  # noqa: E402
from app.core import graph  # noqa: E402
from app.core.context import CaseContext  # noqa: E402
from scripts.eval_cases import GOLDEN  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "eval_cost_baseline.json"

# A regression this size or larger (tokens/case going UP) fails the gate.
# Improvement (tokens/case going down) never fails -- only cost creeping up
# is the thing worth catching.
REGRESSION_THRESHOLD_PCT = 20.0


def _drain(gen) -> None:
    for _ in gen:
        pass


def _run_case_live(spec: dict) -> dict:
    """Run one golden case through the real pipeline, real LLM calls
    included, and return that case's token usage in isolation."""
    llm.reset_usage_totals()
    ctx = CaseContext.from_case(spec["case"])
    _drain(graph.run_pipeline(ctx))
    if ctx.escalation is None:
        _drain(graph.run_resolution(ctx, via_mediation=spec["via_mediation"]))
    return llm.get_usage_totals()


def measure() -> dict:
    per_case: list[dict] = []
    for spec in GOLDEN:
        usage = _run_case_live(spec)
        per_case.append(usage)
        print(
            f"  [{spec['band']:>10}] {spec['name']:<55} "
            f"prompt={usage['prompt_tokens']:>5} completion={usage['completion_tokens']:>4} "
            f"total={usage['total_tokens']:>5} ({usage['calls']} call(s))"
        )

    n = len(per_case)
    avg_prompt = round(sum(c["prompt_tokens"] for c in per_case) / n, 1)
    avg_completion = round(sum(c["completion_tokens"] for c in per_case) / n, 1)
    avg_total = round(sum(c["total_tokens"] for c in per_case) / n, 1)
    return {
        "cases": n,
        "avg_prompt_tokens_per_case": avg_prompt,
        "avg_completion_tokens_per_case": avg_completion,
        "avg_total_tokens_per_case": avg_total,
    }


def load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def write_baseline(measurement: dict) -> None:
    import datetime

    payload = dict(measurement)
    payload["measured_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote baseline -> {BASELINE_PATH}")


def main() -> int:
    if not os.getenv("SARVAM_API_KEY"):
        print("SARVAM_API_KEY not set -- this script measures real API cost and needs a live key. Aborting.")
        return 1

    force_write = "--write-baseline" in sys.argv

    print(f"Measuring live token usage across {len(GOLDEN)} golden eval cases...\n")
    measurement = measure()

    print(
        f"\n{'='*70}\n"
        f"avg prompt tokens/case:     {measurement['avg_prompt_tokens_per_case']}\n"
        f"avg completion tokens/case: {measurement['avg_completion_tokens_per_case']}\n"
        f"avg TOTAL tokens/case:      {measurement['avg_total_tokens_per_case']}"
    )

    baseline = load_baseline()
    if force_write or baseline is None:
        if baseline is None:
            print("\nNo baseline on record yet -- writing this run as the baseline.")
        write_baseline(measurement)
        return 0

    old = baseline["avg_total_tokens_per_case"]
    new = measurement["avg_total_tokens_per_case"]
    pct_change = round(100 * (new - old) / old, 1) if old else 0.0

    print(f"\nBaseline avg total tokens/case: {old} (measured {baseline.get('measured_at', 'unknown')})")
    print(f"This run avg total tokens/case: {new}")
    print(f"Change: {pct_change:+}%")

    if pct_change > REGRESSION_THRESHOLD_PCT:
        print(
            f"\nFAIL: token cost per eval case regressed {pct_change}%, "
            f"exceeding the {REGRESSION_THRESHOLD_PCT}% threshold."
        )
        return 1

    print(f"\nOK: within the {REGRESSION_THRESHOLD_PCT}% regression threshold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
