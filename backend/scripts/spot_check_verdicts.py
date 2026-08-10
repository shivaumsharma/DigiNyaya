"""Manual, human-in-the-loop spot check of judge_real_outcomes.py's automated
match/mismatch grading -- built because the calibration diagnostic in
scripts/calibrate_safety_gate.py found composite_confidence (and every other
signal checked) NEGATIVELY correlated with the grader's own verdict on the
27 real-judgment cases that have one (AUC ~0.36-0.4, r ~ -0.23 to -0.29,
consistently in the same wrong direction across independent signals -- not
the scatter-around-0.5 you'd expect from pure noise). That consistent sign
flip has two explanations: a genuine (interesting) finding about where the
scripted mediation formula misses equitable nuance, or a labeling bug in the
grader (e.g. a flipped claimant/respondent convention). This script exists
to rule out the second, cheaper explanation before trusting the first.

CRITICAL: this must be run and answered by an actual human (you), not by an
LLM, including the one that helped write this script. Having an LLM grade
these labels would just be a second automated grader -- it would NOT rule
out a systematic bug shared between it and the first one, which is exactly
the failure mode being checked for. The automated grader's own verdict and
reasoning are deliberately hidden until AFTER you answer, so your read isn't
anchored by its explanation.

Usage (from the backend folder, interactive -- run it yourself, in your own
terminal):
    python -m scripts.spot_check_verdicts              # 10 random cases
    python -m scripts.spot_check_verdicts --n 15
    python -m scripts.spot_check_verdicts --all         # all 27
    python -m scripts.spot_check_verdicts --seed 42     # reproducible sample
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from scripts.ingest_judgments import _window, html_to_text  # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
VERDICTS_PATH = _BACKEND / "data_cache" / "real_judgment_verdict_comparison.json"
JUDGMENT_CACHE_DIR = _BACKEND / "data_cache" / "indiankanoon"
OUT_DIR = _BACKEND / "data_cache"

VALID_LABELS = {"match", "partial", "mismatch"}


def _load_judgment_text(case_id: str) -> str | None:
    tid = case_id.replace("IK-EVAL-", "")
    path = JUDGMENT_CACHE_DIR / f"{tid}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return html_to_text(raw.get("doc", ""))


def _prompt_label(case_num: int, total: int) -> str:
    while True:
        raw = input(f"[{case_num}/{total}] Your label (match / partial / mismatch / skip / quit): ").strip().lower()
        if raw in VALID_LABELS or raw in ("skip", "quit", "q"):
            return raw
        print(f"  Please answer one of: {', '.join(sorted(VALID_LABELS))}, skip, or quit.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10, help="number of cases to sample (default 10)")
    ap.add_argument("--all", action="store_true", help="review all cases instead of a sample")
    ap.add_argument("--seed", type=int, default=None, help="random seed, for a reproducible sample")
    args = ap.parse_args()

    if not VERDICTS_PATH.exists():
        print(f"Not found: {VERDICTS_PATH}. Run scripts/judge_real_outcomes.py first.")
        return

    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    if args.seed is not None:
        random.seed(args.seed)
    sample = list(verdicts) if args.all else random.sample(verdicts, min(args.n, len(verdicts)))

    print(f"\nSpot-checking {len(sample)} of {len(verdicts)} real-judgment case(s).")
    print("The automated grader's own verdict is hidden until after you answer each one.\n")

    results = []
    for i, case in enumerate(sample, 1):
        case_id = case["case_id"]
        print("=" * 78)
        print(f"Case {case_id}  ({case.get('category', 'unknown category')})")
        print("-" * 78)

        judgment_text = _load_judgment_text(case_id)
        if judgment_text is None:
            print("(No cached judgment text found for this case -- skipping.)\n")
            continue
        print("REAL JUDGMENT (head + tail -- the operative order is usually near the end):\n")
        print(_window(judgment_text, head=2500, tail=2000))
        print()
        print("-" * 78)
        print(f"AI's own relief output: {case.get('ai_relief', '(not recorded)')}")
        print("(Note: only the relief amount was cached, not the full AI order text or")
        print(" relief type -- e.g. 'Rs. 0' could mean dismissed OR a non-monetary order")
        print(" with no incidental damages. Judge as best you can from what's shown.)")
        print("-" * 78)

        label = _prompt_label(i, len(sample))
        if label in ("quit", "q"):
            print("\nStopping early.")
            break
        if label == "skip":
            print()
            continue

        auto_verdict = case["verdict"]
        agree = label == auto_verdict
        print(f"\n  Automated grader said: {auto_verdict!r} -- {case.get('reason', '(no reason recorded)')}")
        print(f"  {'AGREE' if agree else '*** DISAGREE ***'}\n")

        results.append({
            "case_id": case_id,
            "category": case.get("category"),
            "manual_label": label,
            "automated_verdict": auto_verdict,
            "automated_reason": case.get("reason"),
            "agree": agree,
        })

    if not results:
        print("No cases labeled -- nothing to summarize.")
        return

    n_agree = sum(1 for r in results if r["agree"])
    n_total = len(results)
    print("=" * 78)
    print(f"SUMMARY: {n_agree}/{n_total} manual labels agreed with the automated grader ({n_agree/n_total:.0%}).")
    disagreements = [r for r in results if not r["agree"]]
    if disagreements:
        print(f"\n{len(disagreements)} disagreement(s):")
        for r in disagreements:
            print(f"  {r['case_id']}: you said {r['manual_label']!r}, grader said {r['automated_verdict']!r}")
        print(
            "\nIf this is a meaningful chunk of the sample, that's evidence of a grader bug"
            " (check judge_real_outcomes.py's claimant/respondent labeling convention next),"
            " not a real negative finding about the mediation formula."
        )
    else:
        print("\nNo disagreements -- the automated grader's labels held up under manual review.")
        print(
            "This turns the earlier finding into a genuine (if small-n) result worth writing"
            " up honestly, rather than a suspected bug."
        )

    out_path = OUT_DIR / f"spot_check_results_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
