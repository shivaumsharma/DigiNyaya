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

INCREMENTAL BY DEFAULT, two levels:
  1. Scripted-mode re-runs skip re-executing the pipeline ENTIRELY for any
     case where the pipeline's source code hasn't changed since the last
     run (scripted mode is a pure function of case + code, so there's
     nothing to learn from re-running it) -- see _pipeline_fingerprint().
  2. Otherwise (code changed, or --live-llm), the pipeline is re-run but the
     judge() LLM call is skipped whenever the resulting AI output hashes
     the same as a prior run's -- only cases whose ACTUAL resolution
     changed get a fresh (paid) judge call.
Pass --fresh to ignore all caching and redo everything from scratch.

Run (from backend/): python -m scripts.judge_real_outcomes
"""
from __future__ import annotations

import argparse
import hashlib
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
    """Scope DIGINYAYA_USE_LLM to a block only. app.llm.client._llm_disabled()
    reads this env var globally with no scoping of its own -- setting it for
    the whole process (as an earlier version of this script did) also
    silently kills this script's OWN judge() calls later, since they go
    through the same llm.generate_json() gate. Restores whatever value (or
    absence) was there before on exit, rather than assuming "1".

    `value` defaults to "0" (fast/free scripted mode, the default for
    day-to-day re-runs). Pass "1" (via --live-llm) to let analysis.py's
    LLM-based defense-strength judgment and mediation.py's LLM reasoning
    actually run during the eval -- real per-case Sarvam cost, only worth
    paying when specifically testing the live-LLM path's accuracy."""

    def __init__(self, value: str = "0"):
        self._value = value

    def __enter__(self):
        self._prev = os.environ.get("DIGINYAYA_USE_LLM")
        os.environ["DIGINYAYA_USE_LLM"] = self._value
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("DIGINYAYA_USE_LLM", None)
        else:
            os.environ["DIGINYAYA_USE_LLM"] = self._prev


def run_and_capture(case: dict, *, live_llm: bool = False) -> dict | None:
    """Re-run one case through the pipeline (scripted/fast by default -- see
    _llm_disabled_for) to capture full resolution details. Returns None if
    it escalated (nothing to compare -- no AI resolution was produced)."""
    ctx = _build_ctx(case)
    with _llm_disabled_for("1" if live_llm else "0"):
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


# Source files that determine the SCRIPTED/deterministic pipeline's output
# for a given case. Deliberately excludes anything only reachable via a live
# LLM call (whose output isn't a pure function of this repo's code anyway).
_PIPELINE_SOURCE_FILES = [
    "app/agents/nlp.py", "app/agents/analysis.py", "app/agents/mediation.py",
    "app/agents/resolution.py", "app/agents/ingestion.py", "app/agents/research.py",
    "app/core/safety_gate.py", "app/core/graph.py",
    "app/data/loader.py", "app/data/precedents.json",
]
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _pipeline_fingerprint() -> str:
    """Hash of every source file that affects the deterministic (scripted)
    pipeline's output. In scripted mode the pipeline is a pure function of
    (case input, this code) -- if neither changed since the last run,
    re-executing it would produce byte-identical output, so there's nothing
    to learn from doing so. Used to skip run_and_capture() ENTIRELY (not
    just the judge() call) for scripted-mode re-runs where nothing relevant
    changed. Deliberately NOT trusted for --live-llm runs, where the actual
    model call means re-running can genuinely produce different output even
    with identical code -- see judge_real_outcomes.py's docstring."""
    h = hashlib.sha256()
    for rel in _PIPELINE_SOURCE_FILES:
        p = _BACKEND_ROOT / rel
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


def _ai_hash(ai: dict) -> str:
    """Deterministic fingerprint of everything judge() actually reads from
    `ai` (relief_type, amount, order text). Used to cache verdicts by
    case_id -- as long as a case's ACTUAL AI output hasn't changed since the
    last run, the cached verdict is still correct and re-asking the judge
    LLM would just re-pay for the identical question. Only cases whose
    resolution genuinely changed (because a real code fix touched them) get
    re-judged on the next run."""
    material = {
        "relief_type": ai.get("relief_type"),
        "relief_amount": ai.get("relief_amount"),
        "order": ai.get("order"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


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
    # temperature=0.0, not generate_json's 0.2 default: this is a comparison/
    # classification judgment, not creative drafting -- the same AI decision
    # compared against the same real outcome should always get the same
    # verdict. Found via direct inspection: IK-EVAL-4862458's own `reason`
    # field said the court "denied application" while `real_outcome` (fed to
    # the SAME judge call) said the court "granted the plaintiff's
    # application" -- an internally contradictory verdict, i.e. judge noise,
    # not a real pipeline error.
    # max_tokens=2000, not 4096: this call resolves to the fast-tier model
    # (sarvam_fast_model), and Sarvam's newer sarvam-105b-conversations --
    # the replacement for the now-deprecated sarvam-30b, see app/llm/config.py
    # -- caps max_tokens at 2048 on the starter subscription tier, lower than
    # sarvam-30b's old 4096 ceiling. Confirmed via a raw API call: requesting
    # 4096 here 400s outright ("exceeds the maximum allowed... 2048"), not a
    # graceful truncation. The docstring above already established this
    # call's real completions finish in ~1200-1700 tokens, so 2000 keeps
    # the same headroom margin the original 4096 was providing, just under
    # the new model's actual ceiling.
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=2000, temperature=0.0)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Judge AI resolutions against real court outcomes.")
    ap.add_argument("--live-llm", action="store_true",
                     help="let analysis.py/mediation.py's own LLM reasoning run during the pipeline "
                          "re-run (real per-case Sarvam cost), instead of the default fast/free "
                          "scripted mode. The judge() call itself always uses the LLM either way.")
    ap.add_argument("--fresh", action="store_true",
                     help="ignore any cached verdicts in the existing output file and re-judge every "
                          "case from scratch (re-pays for all of them). Default is incremental: a "
                          "case is only re-judged if its actual AI output (relief type/amount/order) "
                          "changed since the last run.")
    args = ap.parse_args()

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not llm.is_available():
        print("ERROR: LLM unavailable -- the judge step needs a real LLM call. Aborting.")
        return 1
    if args.live_llm:
        print("--live-llm: analysis.py/mediation.py will make real LLM calls during this run.")

    # Cache keyed by case_id -> {ai_hash, verdict, reason, ai_relief,
    # real_outcome, code_fingerprint, live_llm}. Two levels of reuse:
    #  1. Full skip (no pipeline execution, no judge call at all) when this
    #     run is scripted mode, the cached entry was ALSO scripted mode, and
    #     the pipeline code hasn't changed since (see _pipeline_fingerprint).
    #     Scripted mode is a pure function of (case, code) -- nothing to
    #     learn from re-running it.
    #  2. Judge-only skip (pipeline re-run, but reuse the verdict) whenever
    #     the freshly-computed AI output hashes the same as before -- covers
    #     --live-llm runs (which always re-execute the pipeline, since a live
    #     model call can genuinely differ run to run) and scripted runs where
    #     the code fingerprint changed but this SPECIFIC case's output didn't.
    cache: dict[str, dict] = {}
    if not args.fresh and OUT_PATH.exists():
        prior = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        cache = {r["case_id"]: r for r in prior if r.get("ai_hash")}
        print(f"Loaded {len(cache)} cached verdict(s) from {OUT_PATH} -- only re-judging cases whose "
              "AI output changed (use --fresh to ignore and re-judge everything).")

    current_fingerprint = _pipeline_fingerprint()

    results = []
    n_full_skip = 0
    n_cached = 0
    n_judged = 0
    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] {case['case_id']} ({case['category']})...", end=" ", flush=True)

        cached = cache.get(case["case_id"])
        if (
            cached
            and not args.live_llm
            and cached.get("live_llm") is False
            and cached.get("code_fingerprint") == current_fingerprint
        ):
            print(f"(unchanged, pipeline not re-run) {cached['verdict']} -- {(cached.get('reason') or '')[:80]}")
            results.append(cached)
            n_full_skip += 1
            continue

        ai = run_and_capture(case, live_llm=args.live_llm)
        if ai is None:
            print("skipped (escalated -- no AI resolution to compare)")
            continue
        ai_hash = _ai_hash(ai)
        if cached and cached.get("ai_hash") == ai_hash:
            print(f"(cached) {cached['verdict']} -- {(cached.get('reason') or '')[:80]}")
            reused = dict(cached)
            reused["code_fingerprint"] = current_fingerprint
            reused["live_llm"] = args.live_llm
            results.append(reused)
            n_cached += 1
            continue
        n_judged += 1
        verdict = judge(case, ai)
        if verdict is None or verdict.get("verdict") not in ("match", "partial", "mismatch"):
            print("JUDGE FAILED (no usable response)")
            results.append({
                "case_id": case["case_id"], "category": case["category"],
                "verdict": "judge_failed", "reason": None,
                "ai_relief": ai["relief_amount_display"], "ai_hash": ai_hash,
                "code_fingerprint": current_fingerprint, "live_llm": args.live_llm,
            })
            # Saved incrementally so a mid-run crash doesn't lose already-judged cases.
            OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            continue
        print(f"{verdict['verdict']} -- {verdict.get('reason', '')[:80]}")
        results.append({
            "case_id": case["case_id"],
            "category": case["category"],
            "verdict": verdict["verdict"],
            "reason": verdict.get("reason"),
            "ai_relief": ai["relief_amount_display"],
            "real_outcome": case["expected_outcome"][:200],
            "ai_hash": ai_hash,
            "code_fingerprint": current_fingerprint,
            "live_llm": args.live_llm,
        })
        OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.3)

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    counts = {"match": 0, "partial": 0, "mismatch": 0, "judge_failed": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print("\n" + "=" * 70)
    print(f"Compared {len(results)} case(s) with a drafted AI resolution -> {OUT_PATH}")
    print(f"({n_full_skip} unchanged/fully skipped, {n_cached} reused from cache after re-running, "
          f"{n_judged} freshly judged)\n")
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
