"""Report the real escalation rate from the 214-case real-judgment eval run.

Built to put a real number behind the resume claim "tested on 100+ disputes
with 78% resolved without escalation" -- that 78%/100+ figure turned out to
be static marketing copy (frontend/src/i18n/en.json's homepage stat block,
identical across all 11 language files, never computed from real data) with
no actual measurement behind it anywhere in this codebase. This script
computes the real thing from data that already exists:
data_cache/real_judgment_eval_results.json, the output of
scripts/run_real_judgment_eval.py's most recent run against 214 real,
sourced Indian court judgments (see scripts/source_free_judgments.py) pushed
through DigiNyaya's actual 5-agent pipeline.

"Resolved without escalation" here means the case reached a final AI
resolution (`resolved: true`) rather than being stopped by the safety gate
at the pre-filter checkpoint or the post-analysis checkpoint (see
app/core/safety_gate.py) or escalated at the resolution stage itself.

Run (from backend/): python -m scripts.measure_escalation_rate
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
RESULTS_PATH = _DATA_DIR / "real_judgment_eval_results.json"


def main() -> int:
    cases = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    n = len(cases)
    errored = [c for c in cases if c.get("error")]
    ran_ok = [c for c in cases if not c.get("error")]

    resolved = [c for c in ran_ok if c.get("resolved")]
    # "escalated" is set from graph.run_pipeline() alone (safety-gate checkpoints
    # A/B, BEFORE resolution runs); a case that passes those but is then escalated
    # during graph.run_resolution() gets "escalated_at_resolution" instead, and
    # the two are mutually exclusive (see scripts/run_real_judgment_eval.py's
    # run_one() -- "escalated" is never updated after the resolution-stage check
    # runs). Total escalated is therefore the sum of both, which reconciles
    # exactly against n_ran_ok: 36 + 5 + 173 = 214 in the current data.
    escalated_pre_filter = [c for c in ran_ok if c.get("escalated")]
    escalated_at_resolution = [c for c in ran_ok if c.get("escalated_at_resolution")]
    n_total_escalated = len(escalated_pre_filter) + len(escalated_at_resolution)

    resolved_rate = round(len(resolved) / len(ran_ok), 3) if ran_ok else None
    escalation_rate = round(n_total_escalated / len(ran_ok), 3) if ran_ok else None

    report = {
        "source": str(RESULTS_PATH.name),
        "n_real_cases_sourced": n,
        "n_pipeline_errors": len(errored),
        "n_ran_cleanly": len(ran_ok),
        "n_resolved_without_escalation": len(resolved),
        "resolved_without_escalation_rate": resolved_rate,
        "n_escalated_at_pre_filter_checkpoints": len(escalated_pre_filter),
        "n_escalated_at_resolution_stage": len(escalated_at_resolution),
        "n_total_escalated": n_total_escalated,
        "escalation_rate": escalation_rate,
        "reconciles": len(resolved) + n_total_escalated == len(ran_ok),
        "note": (
            "All 214 cases are real, sourced Indian court judgments (not synthetic), "
            "run through the actual scripted pipeline, not a demo/marketing figure."
        ),
    }

    print(json.dumps(report, indent=2))
    out_path = _DATA_DIR / "escalation_rate_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
