"""Compare the ORIGINAL evaluation with the LEAKAGE-FREE CONTROL on exactly the same cases.

Inputs (all produced by the workflow in docs/LEAKAGE_FREE_CONTROL.md):
  --original-verdicts   judge_real_outcomes verdicts for the original inputs (default: the v2 file)
  --control-verdicts    verdicts from running the same judge on the control cases
  --control-cases       eval_leakfree_control.json   (inputs rebuilt from pre-decision text only)
  --original-cases      eval_leakfree_original.json  (the original inputs of the same cases)

For the cases judged in BOTH runs it reports, original vs control:
  * winner accuracy, macro-F1 and full-match rate, each with a paired bootstrap 95% CI on the difference and an
    exact McNemar test (so a drop is distinguished from noise);
  * the share of cases whose predicted winner changed;
  * the amount metric (AI amount within +/-20% of a real figure), which the 62% "amount echo" finding puts in doubt;
  * the text-only baseline's lift over the majority rate on the original vs the control descriptions.

HOW TO READ IT. The control removes the court's decision from every input, so it is the cleanest available estimate
of what the pipeline can do WITHOUT any chance of the answer being in the question. If the control is clearly lower,
the headline was inflated by inputs that saw the decision; if the CI includes zero, no inflation is detectable at this
sample size (the CI width tells you how large an inflation this experiment could have missed).

Run (from backend/):  python -m scripts.leakfree_control_report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def _load(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def paired_stats(pairs: list[tuple[int, int]]) -> dict:
    """pairs = [(original, control)] of 0/1 outcomes. Difference is control - original."""
    from scripts.compare_eval_runs import bootstrap_diff
    from scripts.selective_guarantee import mcnemar_exact

    n = len(pairs)
    up = sum(1 for a, b in pairs if b > a)      # control better
    down = sum(1 for a, b in pairs if b < a)    # control worse
    diff = (sum(b for _, b in pairs) - sum(a for a, _ in pairs)) / n if n else 0.0
    lo, hi = bootstrap_diff(pairs) if n >= 2 else (0.0, 0.0)
    return {"n": n, "original": sum(a for a, _ in pairs) / n if n else None, "control": sum(b for _, b in pairs) / n if n else None,
            "diff": diff, "ci95": (lo, hi), "control_better": up, "control_worse": down, "mcnemar_p": mcnemar_exact(up, down)}


def compare_runs(orig_rows: list[dict], ctl_rows: list[dict], dataset: dict[str, dict]) -> dict:
    """Pure comparison of two verdict sets on the cases judged in both."""
    from scripts.compare_eval_runs import GOOD, amount_metrics, summarise

    o = {r["case_id"]: r for r in orig_rows if r.get("verdict") in GOOD}
    c = {r["case_id"]: r for r in ctl_rows if r.get("verdict") in GOOD}
    ids = sorted(set(o) & set(c))
    ob, cb = [o[i] for i in ids], [c[i] for i in ids]
    out: dict = {"n_common": len(ids), "n_original_only": len(set(o) - set(c)), "n_control_only": len(set(c) - set(o))}
    if not ids:
        return out
    so, sc = summarise(ob), summarise(cb)
    out["summary"] = {"original": so, "control": sc}
    out["winner_accuracy"] = paired_stats([(int(bool(a["claimant_prevailed_ai"]) == bool(a["claimant_prevailed_real"])),
                                            int(bool(b["claimant_prevailed_ai"]) == bool(b["claimant_prevailed_real"]))) for a, b in zip(ob, cb)])
    out["full_match"] = paired_stats([(int(a["verdict"] == "match"), int(b["verdict"] == "match")) for a, b in zip(ob, cb)])
    out["direction_changed"] = sum(bool(a["claimant_prevailed_ai"]) != bool(b["claimant_prevailed_ai"]) for a, b in zip(ob, cb))
    out["amount"] = {"original": amount_metrics(ob, dataset), "control": amount_metrics(cb, dataset)}
    return out


def interpret(label: str, st: dict) -> str:
    lo, hi = st["ci95"]
    d, half = 100 * st["diff"], 100 * (hi - lo) / 2
    if hi < 0:
        return (f"{label}: control is LOWER by {abs(d):.1f} points (95% CI {100 * lo:+.1f} to {100 * hi:+.1f}, McNemar p = "
                f"{st['mcnemar_p']:.2g}). The original figure was inflated by inputs that saw the decision.")
    if lo > 0:
        return (f"{label}: control is HIGHER by {d:.1f} points (95% CI {100 * lo:+.1f} to {100 * hi:+.1f}). Unexpected; check the "
                "control inputs for something that makes the case easier (e.g. a cleaner description).")
    return (f"{label}: no detectable difference ({d:+.1f} points, 95% CI {100 * lo:+.1f} to {100 * hi:+.1f}, McNemar p = {st['mcnemar_p']:.2g}). "
            f"An inflation larger than about {half:.1f} points is unlikely at this sample size; smaller ones cannot be ruled out.")


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:5.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--original-verdicts", default=None)
    ap.add_argument("--control-verdicts", default=str(DATA / "leakfree_control_verdicts.json"))
    ap.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    ap.add_argument("--original-cases", default=str(DATA / "eval_leakfree_original.json"))
    args = ap.parse_args()

    ov = Path(args.original_verdicts) if args.original_verdicts else (
        DATA / "real_judgment_verdict_comparison_v2.json" if (DATA / "real_judgment_verdict_comparison_v2.json").exists()
        else DATA / "real_judgment_verdict_comparison.json")
    paths = {"original verdicts": ov, "control verdicts": Path(args.control_verdicts),
             "control cases": Path(args.control_cases), "original cases": Path(args.original_cases)}
    missing = [f"{k}: {v}" for k, v in paths.items() if not v.exists()]
    if missing:
        print("Not found:\n  " + "\n  ".join(missing) + "\nSee docs/LEAKAGE_FREE_CONTROL.md for the steps that create them.")
        return 1

    control_cases, original_cases = _load(paths["control cases"]), _load(paths["original cases"])
    dataset = {c["case_id"]: c for c in control_cases}
    res = compare_runs([r for r in _load(ov) if r["case_id"] in dataset], _load(paths["control verdicts"]), dataset)
    print(f"Cases judged in BOTH runs: {res['n_common']}  (original-only {res['n_original_only']}, control-only {res['n_control_only']})\n")
    if not res["n_common"]:
        print("Nothing to compare.")
        return 1

    so, sc = res["summary"]["original"], res["summary"]["control"]
    print(f"{'metric':32s}{'original':>10s}{'control':>10s}{'change':>10s}")
    for label, a, b in (("winner accuracy", res["winner_accuracy"]["original"], res["winner_accuracy"]["control"]),
                        ("macro-F1", so["macro_f1"], sc["macro_f1"]),
                        ("full-match rate", res["full_match"]["original"], res["full_match"]["control"]),
                        ("recall when claimant wins", so["claimant"]["r"], sc["claimant"]["r"]),
                        ("recall when respondent wins", so["respondent"]["r"], sc["respondent"]["r"])):
        print(f"{label:32s}{_pct(a):>10s}{_pct(b):>10s}{100 * (b - a):+9.1f}pt")
    ao, ac = res["amount"]["original"], res["amount"]["control"]
    print(f"{'AI amount within +/-20%':32s}{_pct(ao['within_20pct']):>10s}{_pct(ac['within_20pct']):>10s}   (n {ao['n']} / {ac['n']})")
    print(f"\nPredicted winner changed in {res['direction_changed']} of {res['n_common']} cases.\n")
    print(interpret("Winner accuracy", res["winner_accuracy"]))
    print(interpret("Full match", res["full_match"]))

    # text-only baseline on the original vs control descriptions, same cases
    from scripts.audit_outcome_leakage import description_only_cv
    ids = set(c["case_id"] for c in control_cases)
    vo = {r["case_id"]: r for r in _load(ov)}
    vc = {r["case_id"]: r for r in _load(paths["control verdicts"])}
    common = ids & set(vo) & set(vc)
    cv_o = description_only_cv([c for c in original_cases if c["case_id"] in common], vo)
    cv_c = description_only_cv([c for c in control_cases if c["case_id"] in common], vc)
    print("\nText-only baseline (cross-validated, same cases):")
    for label, cv in (("original descriptions", cv_o), ("control descriptions ", cv_c)):
        if cv is None:
            print(f"  {label}: skipped (too few cases in a class, or scikit-learn missing)")
        else:
            print(f"  {label}: n={cv['n']}  accuracy={_pct(cv['cv_accuracy'])}  majority={_pct(cv['majority_rate'])}  "
                  f"lift={100 * cv['lift_over_majority']:+.1f}pt  macro-F1={cv['macro_f1']:.3f}")
    print("  If the lift shrinks on control descriptions, part of the original baseline's edge came from description wording shaped by the decision.")
    print("\nCaveats: the control cases are a sample, not the whole corpus; the control inputs are LLM-written too (a "
          "cleaner narrative can make a case easier, which this would show as 'higher'); and the verdicts still come "
          "from an LLM judge that has not been validated against human labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
