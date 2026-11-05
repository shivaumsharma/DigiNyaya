"""Why is small-claims / debt recovery the weakest category, and can a category-specific calibration help? No LLM calls.

Small claims is 64% of the corpus, escalates in 50% of cases and has the lowest accuracy against its own baseline
(71.8% vs 88.7% for always-claimant). Part 1 splits its judged cases into failure modes; part 2 tests one honest
calibration on held-out cases.

PART 1, failure modes (judged cases, from the judge's stored fields):
  too generous       the AI decided for the claimant, the court decided for the respondent
  wrongly dismissed  the AI dismissed (decided for the respondent), the court decided for the claimant
  right side, wrong relief   same winner, but the judge scored the relief only "partial" or "mismatch"
  right and full     same winner, full match
Also how many wrong-side cases were near ties (claimant and respondent strength within 0.05).

PART 2, calibration: the scripted pipeline dismisses whenever respondent strength >= claimant strength. A shift `d` added
to claimant strength (small claims only) moves the dismissal boundary. Using the proxy winner "relief type is not
dismissed", d is chosen on a random half and evaluated on the other half. Because small claims is 89% claimant wins,
accuracy rewards shifting everything toward "claimant", so d is chosen by MACRO-F1, and both numbers are reported.
Run from backend/:  python -m scripts.small_claims_analysis
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
CATEGORY = "small_claims_debt_recovery"
SHIFTS = (-0.05, 0.0, 0.03, 0.05, 0.08, 0.10, 0.15)


def failure_mode(v: dict) -> str:
    ai, real = bool(v["claimant_prevailed_ai"]), bool(v["claimant_prevailed_real"])
    if ai and not real:
        return "too generous (AI claimant, court respondent)"
    if not ai and real:
        return "wrongly dismissed (AI respondent, court claimant)"
    return "right side, full match" if v["verdict"] == "match" else "right side, wrong relief or amount"


def macro_f1(ys: list[bool], preds: list[bool]) -> float:
    def f1(t, p, f):
        return 0.0 if t == 0 else 2 * t / (2 * t + p + f)
    tp = sum(a and b for a, b in zip(ys, preds))
    tn = sum((not a) and (not b) for a, b in zip(ys, preds))
    fp = sum((not a) and b for a, b in zip(ys, preds))
    fn = sum(a and (not b) for a, b in zip(ys, preds))
    return (f1(tp, fp, fn) + f1(tn, fn, fp)) / 2


def half(case_id: str) -> int:
    return int(hashlib.sha256(f"sc:{case_id}".encode()).hexdigest()[:8], 16) % 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--results", default=str(DATA / "real_judgment_eval_results.json"))
    ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison_v2.json"))
    ap.add_argument("--out", default=str(DATA / "small_claims_report.json"))
    args = ap.parse_args()
    os.environ["DIGINYAYA_USE_LLM"] = "0"

    cases = {c["case_id"]: c for c in json.loads(Path(args.dataset).read_text(encoding="utf-8"))}
    res = {r["case_id"]: r for r in json.loads(Path(args.results).read_text(encoding="utf-8"))}
    ver = {v["case_id"]: v for v in json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
           if v.get("verdict") in ("match", "partial", "mismatch") and v.get("claimant_prevailed_real") is not None}

    report: dict = {}
    for label, pick in (("small claims", lambda c: c == CATEGORY), ("all other categories", lambda c: c != CATEGORY)):
        ids = [i for i in ver if pick(cases[i]["category"])]
        modes = Counter(failure_mode(ver[i]) for i in ids)
        wrong = [i for i in ids if bool(ver[i]["claimant_prevailed_ai"]) != bool(ver[i]["claimant_prevailed_real"])]
        near = [i for i in wrong if res[i].get("claimant_strength_score") is not None and res[i].get("respondent_strength_score") is not None
                and abs(res[i]["claimant_strength_score"] - res[i]["respondent_strength_score"]) <= 0.05]
        report[label] = {"n": len(ids), "modes": {k: v / len(ids) for k, v in modes.items()}, "wrong_side": len(wrong),
                         "wrong_side_near_tie": len(near)}
        print(f"\n{label}: {len(ids)} judged cases")
        for k, v in modes.most_common():
            print(f"  {k:<52}{100 * v / len(ids):5.1f}%")
        print(f"  wrong-side cases within 0.05 of a tie: {len(near)} of {len(wrong)} ({100 * len(near) / max(1, len(wrong)):.0f}%)")

    # part 2
    from app.agents import mediation
    from app.core import graph
    from scripts.run_real_judgment_eval import _build_ctx

    sc_ids = [i for i in ver if cases[i]["category"] == CATEGORY and res[i].get("resolved")]
    rows = []
    for i in sc_ids:
        ctx = _build_ctx(cases[i])
        list(graph.run_pipeline(ctx))
        if ctx.analysis is None or ctx.mediation is None:
            continue
        rows.append((i, ctx, bool(ver[i]["claimant_prevailed_real"])))

    def winner(ctx, shift):
        s = ctx.analysis.strength_score
        saved = dict(s)
        ctx.analysis.strength_score = {**s, "claimant": min(max(round(s["claimant"] + shift, 2), 0.1), 0.97)}
        try:
            return mediation.run(ctx).output.type != "dismissed"
        finally:
            ctx.analysis.strength_score = saved

    def evaluate(sub, shift):
        ys, ps = [y for _, _, y in sub], [winner(ctx, shift) for _, ctx, _ in sub]
        return {"n": len(ys), "accuracy": sum(a == b for a, b in zip(ys, ps)) / len(ys), "macro_f1": macro_f1(ys, ps),
                "claimant_rate": sum(ps) / len(ps), "respondent_recall": sum((not a) and (not b) for a, b in zip(ys, ps)) / max(1, sum(1 for a in ys if not a))}

    fit, test = [r for r in rows if half(r[0]) == 0], [r for r in rows if half(r[0]) == 1]
    print(f"\nPART 2: shift added to claimant strength, small claims, scripted pipeline, {len(fit)} fit / {len(test)} test cases resolved")
    print(f"{'shift':>6} | {'FIT acc':>8}{'macroF1':>9}{'claim%':>8} | {'TEST acc':>9}{'macroF1':>9}{'claim%':>8}{'resp rec':>9}")
    grid = {}
    for d in SHIFTS:
        f, t = evaluate(fit, d), evaluate(test, d)
        grid[d] = (f, t)
        print(f"{d:>+6.2f} | {100 * f['accuracy']:>7.1f}%{f['macro_f1']:>9.3f}{100 * f['claimant_rate']:>7.0f}% | "
              f"{100 * t['accuracy']:>8.1f}%{t['macro_f1']:>9.3f}{100 * t['claimant_rate']:>7.0f}%{100 * t['respondent_recall']:>8.0f}%")
    best = max(SHIFTS, key=lambda d: grid[d][0]["macro_f1"])
    base_real = sum(y for _, _, y in test) / len(test)
    print(f"\nBest shift on the FIT half by macro-F1: {best:+.2f} -> TEST half accuracy {100 * grid[best][1]['accuracy']:.1f}% "
          f"(shift 0: {100 * grid[0.0][1]['accuracy']:.1f}%), macro-F1 {grid[best][1]['macro_f1']:.3f} (shift 0: {grid[0.0][1]['macro_f1']:.3f}); "
          f"always-claimant on these cases: {100 * base_real:.1f}%")
    report["calibration"] = {"best_shift": best, "grid": {str(d): {"fit": g[0], "test": g[1]} for d, g in grid.items()}}
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
