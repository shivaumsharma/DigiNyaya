"""Pipeline winner accuracy split by how the real case ended (ex parte vs contested vs the rest).

Why: scripts/tag_disposal.py found that about half of the district-court corpus is ex parte decrees, where the defendant
never appeared and the claimant wins almost by default. Accuracy pooled over those and genuinely contested cases hides
what the pipeline does on the contested ones. This script reports, per group: how often the real claimant won (the base
rate a trivial "claimant wins" predictor would score), the pipeline's winner accuracy, and its recall on the cases the
respondent really won.

Inputs: the verdict file from scripts.judge_real_outcomes (default: the v2 file if it exists) and the cases (default:
data_cache/eval_judgments.json), joined on case_id; disposal comes from the judgment text cached under
data_cache/indiankanoon (no LLM, no network). The disposal tag is a keyword heuristic and can be wrong for single cases;
the group-level picture is what to read. Nothing is modified.

Run (from backend/):  python -m scripts.accuracy_by_disposal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
GROUPS = {
    "ex parte decree": {"ex_parte_decreed"},
    "contested (decreed or dismissed)": {"decreed", "dismissed"},
    "ended without a merits decision": {"compromise", "withdrawn", "dismissed_default"},
    "unknown": {"unknown"},
}


def group_of(disposal: str | None) -> str:
    for name, members in GROUPS.items():
        if disposal in members:
            return name
    return "untagged"


def summarise_groups(rows: list[dict]) -> dict:
    """rows: verdict dicts with a 'group' key. Returns per-group counts and rates (None when undefined)."""
    out: dict[str, dict] = {}
    for g in sorted({r["group"] for r in rows}):
        rs = [r for r in rows if r["group"] == g]
        n = len(rs)
        real_win = [r for r in rs if r["claimant_prevailed_real"]]
        real_loss = [r for r in rs if not r["claimant_prevailed_real"]]
        correct = sum(bool(r["claimant_prevailed_ai"]) == bool(r["claimant_prevailed_real"]) for r in rs)
        out[g] = {
            "n": n,
            "claimant_win_rate": len(real_win) / n,
            "pipeline_accuracy": correct / n,
            "respondent_wins": len(real_loss),
            "respondent_win_recall": (sum(not r["claimant_prevailed_ai"] for r in real_loss) / len(real_loss)) if real_loss else None,
            "claimant_win_recall": (sum(bool(r["claimant_prevailed_ai"]) for r in real_win) / len(real_win)) if real_win else None,
        }
        c, r_ = out[g]["claimant_win_recall"], out[g]["respondent_win_recall"]
        out[g]["balanced_accuracy"] = (c + r_) / 2 if c is not None and r_ is not None else None
    return out


def bootstrap_cis(rows: list[dict], reps: int = 2000, seed: int = 11) -> dict | None:
    """95% percentile bootstrap (resampling cases) for balanced accuracy and for pipeline accuracy minus the
    always-claimant base rate. None when there is no case of one of the two kinds (balanced accuracy undefined)."""
    import numpy as np

    real = np.array([bool(r["claimant_prevailed_real"]) for r in rows])
    pred = np.array([bool(r["claimant_prevailed_ai"]) for r in rows])
    if real.all() or (~real).all():
        return None
    rng = np.random.default_rng(seed)
    n = len(rows)
    bal, gap = np.empty(reps), np.empty(reps)
    for i in range(reps):
        idx = rng.integers(0, n, n)
        r, p_ = real[idx], pred[idx]
        if r.all() or (~r).all():
            bal[i] = gap[i] = np.nan
            continue
        bal[i] = ((p_[r]).mean() + (~p_[~r]).mean()) / 2
        gap[i] = (p_ == r).mean() - r.mean()
    return {"balanced_accuracy": tuple(float(x) for x in np.nanpercentile(bal, [2.5, 97.5])),
            "accuracy_minus_base_rate": tuple(float(x) for x in np.nanpercentile(gap, [2.5, 97.5]))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", default=None)
    ap.add_argument("--cases", default=str(DATA / "eval_judgments.json"))
    ap.add_argument("--cache-dir", default=str(DATA / "indiankanoon"))
    args = ap.parse_args()

    from scripts.compare_eval_runs import GOOD
    from scripts.tag_disposal import tag_cases

    vp = Path(args.verdicts) if args.verdicts else (
        DATA / "real_judgment_verdict_comparison_v2.json" if (DATA / "real_judgment_verdict_comparison_v2.json").exists()
        else DATA / "real_judgment_verdict_comparison.json")
    cp = Path(args.cases)
    for p in (vp, cp):
        if not p.exists():
            print(f"Not found: {p}")
            return 1
    cases = [c for c in json.loads(cp.read_text(encoding="utf-8")) if str(c.get("case_id", "")).startswith("DC-EVAL-")]
    tagged, _ = tag_cases(cases, Path(args.cache_dir))
    disposal = {c["case_id"]: (c.get("source") or {}).get("disposal") for c in tagged}
    verdicts = [v for v in json.loads(vp.read_text(encoding="utf-8")) if v.get("verdict") in GOOD and v["case_id"] in disposal]
    rows = [{**v, "group": group_of(disposal[v["case_id"]])} for v in verdicts]
    if not rows:
        print("No judged district-court cases matched.")
        return 1

    s = summarise_groups(rows)
    print(f"Verdicts: {vp.name}  |  {len(rows)} judged district-court cases\n")
    print(f"{'group':36s}{'n':>7s}{'real claimant wins':>20s}{'pipeline acc':>14s}{'resp-win recall':>17s}"
          f"{'claim-win recall':>18s}{'balanced acc':>14s}")
    for g, d in s.items():
        rec = "n/a" if d["respondent_win_recall"] is None else f"{100 * d['respondent_win_recall']:.1f}% ({d['respondent_wins']})"
        cr = "n/a" if d["claimant_win_recall"] is None else f"{100 * d['claimant_win_recall']:.1f}%"
        ba = "n/a" if d["balanced_accuracy"] is None else f"{100 * d['balanced_accuracy']:.1f}%"
        print(f"{g:36s}{d['n']:>7,}{100 * d['claimant_win_rate']:>19.1f}%{100 * d['pipeline_accuracy']:>13.1f}%{rec:>17s}{cr:>18s}{ba:>14s}")
    print("\n95% bootstrap intervals (resampling cases, 2,000 draws):")
    for g in s:
        ci = bootstrap_cis([r for r in rows if r["group"] == g])
        if ci is None:
            print(f"  {g:36s} not defined (all cases have the same real winner)")
            continue
        b, d = ci["balanced_accuracy"], ci["accuracy_minus_base_rate"]
        print(f"  {g:36s} balanced accuracy {100 * b[0]:.1f}% to {100 * b[1]:.1f}%   "
              f"accuracy minus claimant base rate {100 * d[0]:+.1f} to {100 * d[1]:+.1f} points")
    print("  (Intervals cover sampling noise only, not errors in the judge's verdicts or the disposal tag.)")
    c = s.get("contested (decreed or dismissed)")
    if c:
        print(f"\nOn contested cases a 'claimant always wins' predictor scores {100 * c['claimant_win_rate']:.1f}%; "
              f"the pipeline scores {100 * c['pipeline_accuracy']:.1f}%. Only the gap over that base rate is evidence of skill.\n"
              "Balanced accuracy (mean of the two recalls) is the fairer score when one side almost always wins: 50% is chance.")
    print("\nCaveat: disposal is a keyword heuristic; judge verdicts are not yet validated against human labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
