"""State-wise results that are NOT confounded by case mix. No LLM calls.

The headline state numbers (Andhra Pradesh 59.9% resolved vs Telangana 78.1%) mostly reflect different case mixes:
Andhra Pradesh is about 75% small claims, which escalates in about half of cases, while Telangana is about half
property disputes. This script compares the two states WITHIN each category, with a 95% interval on each difference,
and reports each state's results re-weighted to one common category mix (direct standardisation), so that any
remaining gap is a gap between states and not between mixes.

Only states with enough cases are compared (default: at least 200 judged cases in the category).
Run from backend/:  python -m scripts.state_category_analysis --out data_cache/state_category_report.json
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def state_of(case: dict) -> str | None:
    parts = [p.strip() for p in str((case.get("source") or {}).get("court", "")).split(",") if p.strip()]
    return parts[-1] if parts else None


def diff_ci(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float, float]:
    """Difference in proportions p1 - p2 with a normal-approximation 95% interval."""
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return d, d - 1.96 * se, d + 1.96 * se


def standardise(rates_by_cat: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of per-category rates, using a shared category mix. Categories missing from the state are dropped
    and the remaining weights renormalised."""
    use = {c: w for c, w in weights.items() if c in rates_by_cat}
    total = sum(use.values())
    return sum(rates_by_cat[c] * w for c, w in use.items()) / total if total else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--results", default=str(DATA / "real_judgment_eval_results.json"))
    ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison_v2.json"))
    ap.add_argument("--states", default="Andhra Pradesh,Telangana")
    ap.add_argument("--min-n", type=int, default=200)
    ap.add_argument("--out", default=str(DATA / "state_category_report.json"))
    args = ap.parse_args()

    states = [s.strip() for s in args.states.split(",")]
    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    res = {r["case_id"]: r for r in json.loads(Path(args.results).read_text(encoding="utf-8"))}
    ver = {v["case_id"]: v for v in json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
           if v.get("verdict") in ("match", "partial", "mismatch") and v.get("claimant_prevailed_real") is not None}

    cell: dict[tuple[str, str], dict] = defaultdict(lambda: {"n": 0, "resolved": 0, "judged": 0, "correct": 0, "full": 0, "claimant_real": 0})
    for c in cases:
        st = state_of(c)
        if st not in states or c["case_id"] not in res:
            continue
        d = cell[(st, c["category"])]
        d["n"] += 1
        d["resolved"] += 1 if res[c["case_id"]].get("resolved") else 0
        v = ver.get(c["case_id"])
        if v:
            d["judged"] += 1
            d["correct"] += 1 if bool(v["claimant_prevailed_ai"]) == bool(v["claimant_prevailed_real"]) else 0
            d["full"] += 1 if v["verdict"] == "match" else 0
            d["claimant_real"] += 1 if v["claimant_prevailed_real"] else 0

    a, b = states[0], states[1]
    cats = sorted({c for (s, c) in cell if cell[(s, c)]["n"] >= args.min_n and s in (a, b)} &
                  {c for (s, c) in cell if s == a and cell[(s, c)]["n"] >= args.min_n} &
                  {c for (s, c) in cell if s == b and cell[(s, c)]["n"] >= args.min_n})
    report: dict = {"states": states, "categories_compared": cats, "within_category": {}, "standardised": {}}
    print(f"Within-category comparison, {a} minus {b} (difference in percentage points, 95% interval)\n")
    print(f"{'category':<30}{'n '+a[:2]:>8}{'n '+b[:2]:>8}{'resolved':>26}{'winner acc':>26}{'full match':>26}")
    for cat in cats:
        x, y = cell[(a, cat)], cell[(b, cat)]
        row = {"n": {a: x["n"], b: y["n"]}}
        cols = []
        for name, kx, nx, ky, ny in (("resolved", x["resolved"], x["n"], y["resolved"], y["n"]),
                                     ("winner_accuracy", x["correct"], x["judged"], y["correct"], y["judged"]),
                                     ("full_match", x["full"], x["judged"], y["full"], y["judged"])):
            if nx and ny:
                d, lo, hi = diff_ci(kx, nx, ky, ny)
                row[name] = {"diff": d, "ci": [lo, hi], a: kx / nx, b: ky / ny}
                cols.append(f"{100 * kx / nx:4.0f}% vs {100 * ky / ny:4.0f}% ({100 * d:+.0f}, {100 * lo:+.0f}..{100 * hi:+.0f})")
            else:
                cols.append("n/a")
        report["within_category"][cat] = row
        print(f"{cat:<30}{x['n']:>8}{y['n']:>8}" + "".join(f"{c:>26}" for c in cols))

    mix = Counter()
    for (s, c), d in cell.items():
        if c in cats:
            mix[c] += d["n"]
    total = sum(mix.values())
    weights = {c: n / total for c, n in mix.items()}
    print("\nRe-weighted to one common category mix (" + ", ".join(f"{c} {100 * w:.0f}%" for c, w in weights.items()) + "):")
    for name, num, den in (("resolved", "resolved", "n"), ("winner_accuracy", "correct", "judged"), ("full_match", "full", "judged")):
        rates = {s: {c: cell[(s, c)][num] / cell[(s, c)][den] for c in cats if cell[(s, c)][den]} for s in (a, b)}
        sa, sb = standardise(rates[a], weights), standardise(rates[b], weights)
        report["standardised"][name] = {a: sa, b: sb, "diff": sa - sb}
        print(f"  {name:<16} {a}: {100 * sa:5.1f}%   {b}: {100 * sb:5.1f}%   difference {100 * (sa - sb):+.1f} points")
    print("\nUnstandardised, for contrast: " + ", ".join(
        f"{s} resolved {100 * sum(cell[(s, c)]['resolved'] for c in cats) / sum(cell[(s, c)]['n'] for c in cats):.1f}%" for s in (a, b)))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
