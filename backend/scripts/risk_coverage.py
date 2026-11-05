"""Risk-coverage analysis: does abstaining on low-scoring cases buy accuracy, and does a calibrated threshold
actually keep its promise on cases it has not seen?

For every case the pipeline resolved and the judge scored, the AI either named the right winner or not. A SELECTOR
gives each case a score; keeping only the highest-scoring share (the COVERAGE) should lower the error rate (the
RISK). This script compares selectors on the same cases:
  * composite_confidence  -- the pipeline's own confidence (app/core/confidence.py)
  * strength_gap          -- |claimant_strength - respondent_strength| from the analysis agent
  * random                -- the floor any real selector must beat (expected risk = overall error rate)
and reports, per selector, the area under the risk-coverage curve (AURC, lower is better), the risk at fixed
coverage levels, and the Trust-or-Escalate guarantee (scripts/selective_guarantee.py): calibrate a threshold on a
random half so that, with confidence 1 - delta, the kept error rate is at most alpha, then measure the kept error
rate on the OTHER half. Repeated over many random splits, so the headline is how often the promise holds, not one
lucky split.

It uses stored files only (no LLM calls). Inputs: the verdict file (who the AI said won vs who really won) and
real_judgment_eval_results.json (the per-case scores). Run from backend/:  python -m scripts.risk_coverage
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

from scripts.selective_guarantee import fixed_sequence_threshold  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data_cache"
COVERAGES = (1.0, 0.9, 0.75, 0.5, 0.25, 0.1)
ALPHAS = (0.10, 0.15, 0.20)
# Thresholds are on the selector's RANK (0..1, fraction of calibration cases scoring below), fixed in advance and
# independent of the labels, strictest first -- the precondition for fixed-sequence testing.
# It starts at the top 20% (not the top 0.5%): a kept set of a few dozen cases can never be certified by an exact
# bound, which would end the sequence at its first step. Choosing the start from sample SIZE, not labels, is allowed.
RANK_GRID = tuple(round(0.80 - 0.01 * i, 3) for i in range(81))  # 0.80 down to 0.00 (= keep everything)


def load_cases(verdicts_path: Path, results_path: Path) -> list[dict]:
    """One row per resolved+judged case: selector scores, and whether the AI named the right winner."""
    scores = {r["case_id"]: r for r in json.loads(results_path.read_text(encoding="utf-8")) if r.get("resolved")}
    rows = []
    for v in json.loads(verdicts_path.read_text(encoding="utf-8")):
        s = scores.get(v.get("case_id"))
        if s is None or v.get("verdict") not in ("match", "partial", "mismatch"):
            continue
        ai, real = v.get("claimant_prevailed_ai"), v.get("claimant_prevailed_real")
        c, r = s.get("claimant_strength_score"), s.get("respondent_strength_score")
        if ai is None or real is None or c is None or r is None or s.get("composite_confidence") is None:
            continue
        rows.append({
            "case_id": v["case_id"], "category": v.get("category"), "correct": bool(ai) == bool(real),
            "composite_confidence": float(s["composite_confidence"]), "strength_gap": abs(float(c) - float(r)),
            "real_claimant": bool(real), "pred_claimant": bool(ai),
        })
    return rows


def curve(rows: list[dict], key: str) -> list[tuple[float, float]]:
    """(coverage, risk) after keeping the top-k cases by `key`, for every k. Ties are kept together so the curve
    never depends on arbitrary ordering within equal scores."""
    ordered = sorted(rows, key=lambda r: -r[key])
    n, errors, pts, i = len(ordered), 0, [], 0
    while i < n:
        j = i
        while j < n and ordered[j][key] == ordered[i][key]:
            errors += 0 if ordered[j]["correct"] else 1
            j += 1
        pts.append((j / n, errors / j))
        i = j
    return pts


def aurc(rows: list[dict], key: str) -> float:
    """Mean risk over all coverage levels (area under the risk-coverage curve), averaged per case so ties count fairly."""
    ordered = sorted(rows, key=lambda r: -r[key])
    n, errors, total = len(ordered), 0, 0.0
    # tie-aware: every case in a tie group gets the group's risk at the group's full coverage
    i = 0
    while i < n:
        j = i
        while j < n and ordered[j][key] == ordered[i][key]:
            errors += 0 if ordered[j]["correct"] else 1
            j += 1
        total += (j - i) * (errors / j)
        i = j
    return total / n


def risk_at(rows: list[dict], key: str, coverage: float) -> float | None:
    """Error rate among the top `coverage` share by `key` (the smallest tie-closed set reaching that share)."""
    for cov, risk in curve(rows, key):
        if cov >= coverage - 1e-12:
            return risk
    return None


def respondent_recall_at(rows: list[dict], key: str, coverage: float) -> float | None:
    """Among kept cases the court decided for the RESPONDENT, the share the AI also decided for the respondent.
    Guards against a selector that only looks good by keeping easy claimant wins (83% of the corpus)."""
    k = max(1, int(round(coverage * len(rows))))
    kept = sorted(rows, key=lambda r: -r[key])[:k]
    resp = [r for r in kept if not r["real_claimant"]]
    return sum(1 for r in resp if r["correct"]) / len(resp) if resp else None


def rank_scores(cal_values: list[float], values: list[float]) -> list[float]:
    """Fraction of calibration scores strictly below each value: a label-free rank in [0, 1)."""
    s = sorted(cal_values)
    from bisect import bisect_left
    return [bisect_left(s, v) / len(s) for v in values]


def guarantee_trial(rows: list[dict], key: str, alpha: float, delta: float, seed: int) -> dict:
    """One random 50/50 split: calibrate on one half, evaluate the chosen threshold on the other."""
    rng = random.Random(seed)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    half = len(idx) // 2
    cal, test = [rows[i] for i in idx[:half]], [rows[i] for i in idx[half:]]
    cal_vals = [r[key] for r in cal]
    cal_rank = rank_scores(cal_vals, cal_vals)
    res = fixed_sequence_threshold(cal_rank, [r["correct"] for r in cal], alpha, delta, RANK_GRID)
    if res["threshold"] is None:
        return {"certified": False}
    test_rank = rank_scores(cal_vals, [r[key] for r in test])
    kept = [r for r, rk in zip(test, test_rank) if rk >= res["threshold"]]
    if not kept:
        return {"certified": True, "coverage": 0.0, "risk": None, "violated": False}
    risk = sum(0 if r["correct"] else 1 for r in kept) / len(kept)
    return {"certified": True, "coverage": len(kept) / len(test), "risk": risk, "violated": risk > alpha}


def summarise_guarantee(rows: list[dict], key: str, alpha: float, delta: float, trials: int) -> dict:
    out = [guarantee_trial(rows, key, alpha, delta, seed) for seed in range(trials)]
    cert = [o for o in out if o["certified"] and o.get("risk") is not None]
    if not cert:
        return {"certified_share": 0.0}
    return {
        "certified_share": len(cert) / trials,
        "mean_test_coverage": sum(o["coverage"] for o in cert) / len(cert),
        "mean_test_risk": sum(o["risk"] for o in cert) / len(cert),
        "violation_rate": sum(1 for o in cert if o["violated"]) / len(cert),
    }


def side_guarantees(rows: list[dict], key: str, alpha: float, delta: float, trials: int) -> dict:
    """The guarantee, calibrated and tested SEPARATELY for the cases the system decides for the claimant and for the
    respondent. A single pooled threshold can look fine while one side is much worse; this shows each side alone."""
    out = {}
    for side, flag in (("decides_for_claimant", True), ("decides_for_respondent", False)):
        sub = [r for r in rows if r["pred_claimant"] == flag]
        out[side] = {"n": len(sub), "error_rate": (sum(0 if r["correct"] else 1 for r in sub) / len(sub)) if sub else None,
                     **summarise_guarantee(sub, key, alpha, delta, trials)} if len(sub) >= 50 else {"n": len(sub)}
    return out


def build_report(rows: list[dict], delta: float = 0.05, trials: int = 200) -> dict:
    n = len(rows)
    base_risk = sum(0 if r["correct"] else 1 for r in rows) / n
    report = {"n": n, "overall_risk": base_risk, "random_aurc": base_risk, "selectors": {}}
    for key in ("composite_confidence", "strength_gap"):
        report["selectors"][key] = {
            "distinct_values": len({r[key] for r in rows}),
            "aurc": aurc(rows, key),
            "risk_at_coverage": {str(c): risk_at(rows, key, c) for c in COVERAGES},
            "respondent_win_recall_at_50pct": respondent_recall_at(rows, key, 0.5),
            "respondent_win_recall_by_coverage": {str(c): respondent_recall_at(rows, key, c) for c in COVERAGES},
            "per_side_guarantee": {str(a): side_guarantees(rows, key, a, delta, trials) for a in (0.15, 0.20)},
            "guarantee": {str(a): summarise_guarantee(rows, key, a, delta, trials) for a in ALPHAS},
        }
    return report


def print_report(rep: dict, delta: float) -> None:
    print(f"Resolved + judged cases: {rep['n']}   overall winner error rate: {100 * rep['overall_risk']:.1f}%")
    print(f"Random selector AURC = overall error rate = {100 * rep['random_aurc']:.1f}%  (a useful selector must be lower)\n")
    hdr = "  ".join(f"cov {int(c * 100):>3}%" for c in COVERAGES)
    print(f"{'selector':<22} {'AURC':>6}   risk (error rate) at coverage:  {hdr}")
    for key, s in rep["selectors"].items():
        cells = "  ".join(f"{100 * s['risk_at_coverage'][str(c)]:>7.1f}%" if s["risk_at_coverage"][str(c)] is not None else "      - " for c in COVERAGES)
        print(f"{key:<22} {100 * s['aurc']:>5.1f}%   {' ' * 29}{cells}")
    print(f"\nTrust-or-Escalate guarantee (calibrate on a random half, test on the other; 1 - delta = {1 - delta:.0%}):")
    for key, s in rep["selectors"].items():
        for a, g in s["guarantee"].items():
            if g.get("certified_share", 0) == 0:
                print(f"  {key:<22} alpha={float(a):.2f}: could not be certified on any split (escalate everything)")
            else:
                print(f"  {key:<22} alpha={float(a):.2f}: certified in {100 * g['certified_share']:.0f}% of splits, "
                      f"test coverage {100 * g['mean_test_coverage']:.0f}%, test error {100 * g['mean_test_risk']:.1f}%, "
                      f"promise broken in {100 * g['violation_rate']:.1f}% of splits")
    print("\nRespondent-win recall by coverage (the lazy pattern is falling toward 0 as coverage drops):")
    for key, s in rep["selectors"].items():
        cells = "  ".join(f"{int(float(c) * 100):>3}%:{100 * v:>4.0f}%" if v is not None else f"{int(float(c) * 100):>3}%:  -  "
                          for c, v in s["respondent_win_recall_by_coverage"].items())
        print(f"  {key:<22} {cells}")
    print("\nGuarantee per decided side (calibrated and tested separately):")
    for key, s in rep["selectors"].items():
        for a, sides in s["per_side_guarantee"].items():
            for side, g in sides.items():
                if "certified_share" not in g and "error_rate" not in g:
                    print(f"  {key:<22} alpha={float(a):.2f} {side}: too few cases (n={g['n']})")
                elif g.get("certified_share", 0) == 0:
                    print(f"  {key:<22} alpha={float(a):.2f} {side}: n={g['n']}, overall error {100 * g['error_rate']:.0f}%: "
                          "cannot be certified, escalate all")
                else:
                    print(f"  {key:<22} alpha={float(a):.2f} {side}: n={g['n']}, certified in {100 * g['certified_share']:.0f}% of splits, "
                          f"coverage {100 * g['mean_test_coverage']:.0f}%, test error {100 * g['mean_test_risk']:.1f}%, "
                          f"broken in {100 * g['violation_rate']:.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison_v2.json"))
    ap.add_argument("--results", default=str(DATA / "real_judgment_eval_results.json"))
    ap.add_argument("--delta", type=float, default=0.05)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--out", default=str(DATA / "risk_coverage_report.json"))
    args = ap.parse_args()
    rows = load_cases(Path(args.verdicts), Path(args.results))
    if not rows:
        print("No usable cases (need verdicts with claimant_prevailed_ai/real and eval results with scores).")
        return 1
    rep = build_report(rows, args.delta, args.trials)
    Path(args.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print_report(rep, args.delta)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
