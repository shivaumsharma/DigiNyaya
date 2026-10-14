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
    c = s.get("contested (decreed or dismissed)")
    if c:
        print(f"\nOn contested cases a 'claimant always wins' predictor scores {100 * c['claimant_win_rate']:.1f}%; "
              f"the pipeline scores {100 * c['pipeline_accuracy']:.1f}%. Only the gap over that base rate is evidence of skill.\n"
              "Balanced accuracy (mean of the two recalls) is the fairer score when one side almost always wins: 50% is chance.")
    print("\nCaveat: disposal is a keyword heuristic; judge verdicts are not yet validated against human labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
