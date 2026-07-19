"""Compare DigiNyaya's actual resolution against the real court's real
outcome, for every real-judgment case that reached a drafted resolution.

This is the piece scripts/run_real_judgment_eval.py explicitly did NOT do:
that script only checked structural/behavioral things (did it escalate
correctly, did it crash, what confidence score). This script asks the harder
question -- does the AI's actual decision (who wins, roughly how much
relief) match what the real judge actually decided?

Re-runs the pipeline (fast, scripted/deterministic mode -- same as
run_real_judgment_eval.py) to capture the full resolution this time
(relief amount, order text, basis), which the first run didn't save. Then an
LLM judge compares each AI resolution against the real judgment's
expected_outcome (already sourced, real ground truth) and scores it
match / partial / mismatch with a short reason.

Run (from backend/): python -m scripts.judge_real_outcomes
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from app.core import graph  # noqa: E402
# Reuse the same enriched-dataset-preferring path _build_ctx()'s own module
# resolves, so this script and run_real_judgment_eval.py can never silently
# diverge on which dataset (with or without real per-case signals) is used.
from scripts.run_real_judgment_eval import _build_ctx, DATASET_PATH  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "real_judgment_verdict_comparison.json"


class _llm_disabled_for:
    """Scope DIGINYAYA_USE_LLM=0 to a block only. app.llm.client._llm_disabled()
    reads this env var globally with no scoping of its own -- setting it for
    the whole process (as an earlier version of this script did) also
    silently kills this script's OWN judge() calls later, since they go
    through the same llm.generate_json() gate. Restores whatever value (or
    absence) was there before on exit, rather than assuming "1"."""

    def __enter__(self):
        self._prev = os.environ.get("DIGINYAYA_USE_LLM")
        os.environ["DIGINYAYA_USE_LLM"] = "0"
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("DIGINYAYA_USE_LLM", None)
        else:
            os.environ["DIGINYAYA_USE_LLM"] = self._prev


def run_and_capture(case: dict) -> dict | None:
    """Re-run one case through the pipeline (scripted/fast -- see
    _llm_disabled_for) to capture full resolution details. Returns None if
    it escalated (nothing to compare -- no AI resolution was produced)."""
    ctx = _build_ctx(case)
    with _llm_disabled_for():
        list(graph.run_pipeline(ctx))
        if ctx.escalation is not None or ctx.mediation is None:
            return None
        list(graph.run_resolution(ctx, via_mediation=True))
    if ctx.escalation is not None or ctx.resolution is None:
        return None
    res = ctx.resolution
    return {
        "relief_amount": res.relief_amount,
        "relief_amount_display": res.relief_amount_display,
        # med.type carries the PRIMARY relief kind mediation.py decided on --
        # dismissed / full_refund / partial_refund / compensation, or one of
        # the non-monetary kinds (injunction/declaration/replacement/
        # possession). Passing this explicitly (not just an amount + a
        # truncated order) matters: a case testing this exact fix showed the
        # judge reading a correctly-drafted injunction order as "monetary
        # relief" because the prompt led with "Relief: Rs. 8,750" (an
        # INCIDENTAL secondary amount) and only showed order[:2].
        "relief_type": ctx.mediation.type if ctx.mediation else "unknown",
        "basis": res.basis,
        "order": res.order,
        "findings": res.findings,
        "compliance_days": res.compliance_days,
    }


def judge(case: dict, ai: dict) -> dict | None:
    # Kept deliberately terse: a longer prompt (e.g. spelling out detailed
    # definitions of match/partial/mismatch) was observed to make the model
    # reason more before answering, which pushed it past the 4096-token
    # ceiling and silently failed every time -- the short version below
    # reliably finishes in ~1200-1700 tokens total.
    schema = '{"verdict": "match|partial|mismatch", "reason": "<=25 words"}'
    relief_type = ai.get("relief_type", "unknown")
    is_monetary = relief_type in ("full_refund", "partial_refund", "compensation")
    relief_line = (
        f"Relief type: {relief_type}, amount: {ai['relief_amount_display']}"
        if is_monetary
        else f"Relief type: {relief_type} (non-monetary; any Rs. amount below is INCIDENTAL, not the primary relief)"
    )
    prompt = (
        "Compare an AI's dispute decision to what a REAL Indian court actually decided. "
        "'AI DECIDED' always describes the AI's own generic roles: 'the claimant' is whichever party "
        "brought the claim (the plaintiff in the real judgment below, even if that's a bank or company, "
        "not a consumer), and 'the respondent' is the defendant. Map roles by WHO SUED WHOM, not by "
        "which vocabulary sounds like a refund. "
        "match=same side wins, similar relief. partial=same side wins, different relief. "
        "mismatch=opposite side wins, or one side got relief while the real case was dismissed "
        "(or vice versa). A non-monetary relief type (injunction/declaration/replacement/possession) "
        "can still 'match' a real court's non-monetary order even with Rs. 0 -- judge by relief TYPE "
        "and side first, amount second. Answer directly, no long reasoning. JSON only: "
        f"{schema}\n\n"
        f"REAL COURT DECIDED:\n{case['expected_outcome']}\n\n"
        f"AI DECIDED:\n{relief_line}\n"
        f"Full order: {' '.join(ai['order'])}\n"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=4096)
    return data


def main() -> int:
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not llm.is_available():
        print("ERROR: LLM unavailable -- the judge step needs a real LLM call. Aborting.")
        return 1

    results = []
    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] {case['case_id']} ({case['category']})...", end=" ", flush=True)
        ai = run_and_capture(case)
        if ai is None:
            print("skipped (escalated -- no AI resolution to compare)")
            continue
        verdict = judge(case, ai)
        if verdict is None or verdict.get("verdict") not in ("match", "partial", "mismatch"):
            print("JUDGE FAILED (no usable response)")
            results.append({
                "case_id": case["case_id"], "category": case["category"],
                "verdict": "judge_failed", "reason": None,
                "ai_relief": ai["relief_amount_display"],
            })
            continue
        print(f"{verdict['verdict']} -- {verdict.get('reason', '')[:80]}")
        results.append({
            "case_id": case["case_id"],
            "category": case["category"],
            "verdict": verdict["verdict"],
            "reason": verdict.get("reason"),
            "ai_relief": ai["relief_amount_display"],
            "real_outcome": case["expected_outcome"][:200],
        })
        time.sleep(0.3)

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    counts = {"match": 0, "partial": 0, "mismatch": 0, "judge_failed": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print("\n" + "=" * 70)
    print(f"Compared {len(results)} case(s) with a drafted AI resolution -> {OUT_PATH}\n")
    print(f"  MATCH:        {counts['match']}")
    print(f"  PARTIAL:      {counts['partial']}")
    print(f"  MISMATCH:     {counts['mismatch']}")
    print(f"  judge failed: {counts['judge_failed']}")
    scored = len(results) - counts["judge_failed"]
    if scored:
        same_side = counts["match"] + counts["partial"]
        print(f"\n  Exact match (same side AND comparable relief): {counts['match']}/{scored} "
              f"({round(100*counts['match']/scored)}%)")
        # By definition in the judge prompt, "partial" means the same side
        # won but the relief amount/type differs -- pinpointing the exact
        # rupee figure a real court would award is an unreasonably strict
        # bar (two human judges/mediators would rarely converge on an
        # identical number either), so this is the more meaningful accuracy
        # number: did the system correctly identify WHO should prevail.
        print(f"  Directional accuracy (correctly identified the winning side): {same_side}/{scored} "
              f"({round(100*same_side/scored)}%)")

    if counts["mismatch"]:
        print("\nMISMATCHES (worth reading closely):")
        for r in results:
            if r["verdict"] == "mismatch":
                print(f"  - {r['case_id']} ({r['category']}): {r['reason']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
