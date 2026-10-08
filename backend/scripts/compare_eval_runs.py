"""Paired before/after comparison of two judge_real_outcomes.py verdict files
run on the SAME case sample (e.g. the eval sample built by build_eval_sample.py).

Reports, per run and overall:
  - full-match rate, directional accuracy (ruling-only), claimant-class and
    respondent-class precision/recall/F1, macro-F1;
  - the amount metrics the change targets, computed deterministically from the
    stored AI amount vs the rupee figures in the real outcome (no LLM): share of
    direction-correct, claimant-won money cases whose AI amount lands within
    +/-20% of one of the leading real figures, and the median AI/real ratio;
  - a paired case-level breakdown (improved / unchanged / worsened) with a
    bootstrap 95% CI on the full-match difference;
  - a leakage sanity check on the extracted claim amounts.

Run (from backend/):
  python -m scripts.compare_eval_runs --before A.json --after B.json --sample eval_sample.json
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.agents import nlp  # noqa: E402
from scripts.selective_guarantee import mcnemar_exact  # noqa: E402,F401  (re-exported)

MONEY_CATEGORIES = {"small_claims_debt_recovery", "contract_disputes", "consumer_complaints", "tenancy_disputes"}
GOOD = ("match", "partial", "mismatch")


def _load(p: str):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _prf(tp: int, fp: int, fn: int):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def summarise(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("verdict") in GOOD]
    n = len(rows)
    tp = sum(r["claimant_prevailed_ai"] and r["claimant_prevailed_real"] for r in rows)
    fp = sum(r["claimant_prevailed_ai"] and not r["claimant_prevailed_real"] for r in rows)
    fn = sum(not r["claimant_prevailed_ai"] and r["claimant_prevailed_real"] for r in rows)
    tn = n - tp - fp - fn
    cp, cr, cf = _prf(tp, fp, fn)
    rp, rr, rf = _prf(tn, fn, fp)
    return {
        "n": n,
        "full_match": sum(r["verdict"] == "match" for r in rows) / n,
        "directional": (tp + tn) / n,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "claimant": {"p": cp, "r": cr, "f1": cf},
        "respondent": {"p": rp, "r": rr, "f1": rf},
        "macro_f1": (cf + rf) / 2,
    }


def amount_metrics(rows: list[dict], dataset: dict[str, dict]) -> dict:
    """Deterministic amount accuracy on direction-correct, claimant-won cases
    in the money categories that have a parseable real figure and a positive
    AI amount."""
    ratios, hits = [], 0
    for r in rows:
        if r.get("verdict") not in GOOD or r["category"] not in MONEY_CATEGORIES:
            continue
        if not (r["claimant_prevailed_ai"] and r["claimant_prevailed_real"]):
            continue
        ai = r.get("ai_amount")
        if ai is None:
            found = nlp.extract_amounts(r.get("ai_relief") or "")
            ai = found[0] if found else None
        real = [x for x in nlp.extract_amounts(dataset[r["case_id"]]["expected_outcome"])[:3] if x > 0]
        if not ai or ai <= 0 or not real:
            continue
        ratios.append(ai / real[0])
        hits += any(abs(ai - x) <= 0.2 * x for x in real)
    return {
        "n": len(ratios),
        "within_20pct": hits / len(ratios) if ratios else None,
        "median_ai_over_real": st.median(ratios) if ratios else None,
    }


def bootstrap_diff(pairs: list[tuple[int, int]], iters: int = 5000, seed: int = 1) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(pairs)
    diffs = []
    for _ in range(iters):
        s = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(sum(b - a for a, b in s) / n)
    diffs.sort()
    return diffs[int(0.025 * iters)], diffs[int(0.975 * iters)]


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:5.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", default=None, help="optional JSON path for the full report")
    args = ap.parse_args()

    dataset = {c["case_id"]: c for c in _load(args.sample)}
    before = {r["case_id"]: r for r in _load(args.before) if r["case_id"] in dataset}
    after = {r["case_id"]: r for r in _load(args.after) if r["case_id"] in dataset}
    common = sorted(
        cid for cid in before
        if cid in after and before[cid].get("verdict") in GOOD and after[cid].get("verdict") in GOOD
    )
    b_rows, a_rows = [before[c] for c in common], [after[c] for c in common]
    print(f"Sample: {len(dataset)} cases; {len(before)} judged before, {len(after)} after; "
          f"{len(common)} judged in BOTH (paired comparison uses these).\n")

    sb, sa = summarise(b_rows), summarise(a_rows)
    print(f"{'metric':38s}{'before':>10s}{'after':>10s}{'change':>10s}")
    for label, kb, ka in (
        ("full-match rate", sb["full_match"], sa["full_match"]),
        ("directional accuracy", sb["directional"], sa["directional"]),
        ("claimant precision", sb["claimant"]["p"], sa["claimant"]["p"]),
        ("claimant recall", sb["claimant"]["r"], sa["claimant"]["r"]),
        ("claimant F1", sb["claimant"]["f1"], sa["claimant"]["f1"]),
        ("respondent precision", sb["respondent"]["p"], sa["respondent"]["p"]),
        ("respondent recall", sb["respondent"]["r"], sa["respondent"]["r"]),
        ("respondent F1", sb["respondent"]["f1"], sa["respondent"]["f1"]),
        ("macro F1", sb["macro_f1"], sa["macro_f1"]),
    ):
        print(f"{label:38s}{pct(kb):>10s}{pct(ka):>10s}{100 * (ka - kb):+9.1f}pt")
    print(f"\nconfusion before: {sb['confusion']}\nconfusion after : {sa['confusion']}")

    ab, aa = amount_metrics(b_rows, dataset), amount_metrics(a_rows, dataset)
    print("\nAMOUNT accuracy (direction-correct, claimant-won money cases; deterministic, no LLM):")
    print(f"  cases measured             before {ab['n']}  after {aa['n']}")
    print(f"  AI amount within +/-20%   before {pct(ab['within_20pct'])}  after {pct(aa['within_20pct'])}")
    print(f"  median AI / real amount   before {ab['median_ai_over_real']:.2f}  after {aa['median_ai_over_real']:.2f}")

    pairs = [(int(b["verdict"] == "match"), int(a["verdict"] == "match")) for b, a in zip(b_rows, a_rows)]
    up = sum(1 for x, y in pairs if y > x)
    down = sum(1 for x, y in pairs if y < x)
    lo, hi = bootstrap_diff(pairs)
    print(f"\nPaired full-match: {up} cases improved, {down} worsened, {len(pairs) - up - down} unchanged; "
          f"net {100 * (up - down) / len(pairs):+.1f}pt, bootstrap 95% CI [{100 * lo:+.1f}, {100 * hi:+.1f}]pt")
    p_mcnemar = mcnemar_exact(up, down)
    print(f"McNemar exact test on the {up + down} cases that changed: p = {p_mcnemar:.3g} "
          f"({'unlikely to be luck' if p_mcnemar < 0.05 else 'could be luck'}; this says nothing about whether the "
          "judge is right or whether the change was tuned on these same cases)")
    dir_changed = sum(
        b["claimant_prevailed_ai"] != a["claimant_prevailed_ai"] for b, a in zip(b_rows, a_rows)
    )
    print(f"Cases whose predicted winning side changed: {dir_changed}")

    print("\nPer category (full-match, before -> after):")
    cats = collections.defaultdict(list)
    for (b, a) in zip(b_rows, a_rows):
        cats[b["category"]].append((int(b["verdict"] == "match"), int(a["verdict"] == "match")))
    for cat, ps in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat:34s} n={len(ps):4d}  {pct(sum(x for x, _ in ps) / len(ps))} -> {pct(sum(y for _, y in ps) / len(ps))}")

    # Leakage sanity check: extracted claims (only extracted where the
    # description had none) vs description-parsed claims -- if the extractor had
    # read the decree, "claim == real decree" would be far higher for extracted.
    def eq_rate(items):
        eq = tot = 0
        for c in items:
            real = [x for x in nlp.extract_amounts(c["expected_outcome"])[:3] if x > 0]
            claim = c["_claim"]
            if not real or not claim:
                continue
            tot += 1
            eq += any(abs(claim - x) <= 0.05 * x for x in real)
        return (eq / tot if tot else None), tot

    parsed, extracted = [], []
    for c in dataset.values():
        am = nlp.extract_amounts(c["case_description"])
        if am:
            parsed.append({**c, "_claim": am[0]})
        elif c.get("claimed_amount_rupees"):
            extracted.append({**c, "_claim": c["claimed_amount_rupees"]})
    (pr, pn), (er, en) = eq_rate(parsed), eq_rate(extracted)
    print(f"\nLeakage check -- claim within 5% of a real decree figure: description-parsed {pct(pr)} (n={pn}); "
          f"LLM-extracted {pct(er)} (n={en}). Similar rates = no sign the extractor read the decree.")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "before": sb, "after": sa, "amount_before": ab, "amount_after": aa,
            "paired": {"improved": up, "worsened": down, "n": len(pairs), "ci95_pt": [100 * lo, 100 * hi], "mcnemar_exact_p": p_mcnemar},
            "leakage_check": {"parsed": pr, "extracted": er},
        }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
