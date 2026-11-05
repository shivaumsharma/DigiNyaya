"""Can the fine-tuned InLegalBERT and the rule-based pipeline help each other? And does InLegalBERT lean on
decision-shaped wording? No LLM calls: uses the predictions saved by scripts/finetune_inlegalbert.py.

PART 1, wording dependence. On the held-out test cases, InLegalBERT's accuracy on descriptions with no result phrasing
(clean) versus descriptions the leakage audit flags (strong outcome language or a named judge). If it does much better on
flagged ones, it is partly reading the answer.

PART 2, hybrids on the held-out test set (original descriptions) and on the 303 leakage-free control cases (rebuilt
descriptions, where both systems also have a pipeline winner). Rules:
  pipeline only / InLegalBERT only         the two systems alone
  respondent only if BOTH say respondent   favours the claimant, fewer wrongful dismissals
  respondent if EITHER says respondent     favours the respondent
  InLegalBERT when it is confident         follow InLegalBERT if its probability is <= 0.2 or >= 0.8, else the pipeline
  decide only when they AGREE              abstain otherwise; accuracy on the agreed cases and the coverage
Reported: accuracy, macro-F1, respondent-win recall (and coverage where it abstains). A hybrid is only worth building
if it beats BOTH parts on the leakage-free control, not just on the original text.
Run from backend/:  python -m scripts.hybrid_analysis
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from scripts.small_claims_analysis import macro_f1  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def score(rows: list[tuple[bool, bool | None]]) -> dict:
    """rows: (real_claimant, prediction or None = abstained)."""
    decided = [(y, p) for y, p in rows if p is not None]
    if not decided:
        return {"n": 0, "coverage": 0.0}
    ys, ps = [y for y, _ in decided], [p for _, p in decided]
    resp = [p for y, p in decided if not y]
    return {"n": len(decided), "coverage": len(decided) / len(rows), "accuracy": sum(a == b for a, b in zip(ys, ps)) / len(ys),
            "macro_f1": macro_f1(ys, ps), "respondent_recall": (sum(1 for p in resp if not p) / len(resp)) if resp else None}


def rules(p_ilb: float, pipe: bool) -> dict[str, bool | None]:
    ilb = p_ilb >= 0.5
    return {
        "pipeline only": pipe,
        "InLegalBERT only": ilb,
        "respondent only if BOTH say respondent": (ilb or pipe),
        "respondent if EITHER says respondent": (ilb and pipe),
        "InLegalBERT when confident (<=0.2 or >=0.8), else pipeline": ilb if (p_ilb <= 0.2 or p_ilb >= 0.8) else pipe,
        "decide only when they AGREE": ilb if ilb == pipe else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--predictions", default=str(DATA / "inlegalbert_predictions.json"))
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--out", default=str(DATA / "hybrid_report.json"))
    args = ap.parse_args()

    from scripts.audit_outcome_leakage import audit_text

    preds = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    cases = {c["case_id"]: c for c in json.loads(Path(args.dataset).read_text(encoding="utf-8"))}
    report: dict = {}

    # PART 1
    clean, flagged = [], []
    for cid, r in preds.items():
        a = audit_text(cases[cid])
        (flagged if (a["outcome_language"] or a["names_judge"]) else clean).append((bool(r["real_claimant"]), r["p_claimant_original"] >= 0.5))
    pc = lambda rows: (sum(y == p for y, p in rows) / len(rows)) if rows else None  # noqa: E731
    report["wording_dependence"] = {"clean_n": len(clean), "flagged_n": len(flagged), "clean_accuracy": pc(clean), "flagged_accuracy": pc(flagged)}
    print(f"PART 1, InLegalBERT accuracy on clean descriptions {100 * pc(clean):.1f}% (n={len(clean)}) vs flagged "
          f"{('n/a' if not flagged else f'{100 * pc(flagged):.1f}%')} (n={len(flagged)})")

    # PART 2
    sets = {
        "held-out test set, original descriptions": [(bool(r["real_claimant"]), r["p_claimant_original"], bool(r["pipeline_claimant_original"]))
                                                     for r in preds.values() if r.get("pipeline_claimant_original") is not None],
        "leakage-free control cases, rebuilt descriptions": [(bool(r["real_claimant"]), r["p_claimant_control"], bool(r["pipeline_claimant_control"]))
                                                            for r in preds.values()
                                                            if r.get("is_control") and r.get("p_claimant_control") is not None and r.get("pipeline_claimant_control") is not None],
        "same control cases, ORIGINAL descriptions": [(bool(r["real_claimant"]), r["p_claimant_original"], bool(r["pipeline_claimant_original"]))
                                                      for r in preds.values()
                                                      if r.get("is_control") and r.get("p_claimant_control") is not None and r.get("pipeline_claimant_control") is not None
                                                      and r.get("pipeline_claimant_original") is not None],
    }
    for name, rows in sets.items():
        names = list(rules(0.5, True))
        results = {n: score([(y, rules(p, pipe)[n]) for y, p, pipe in rows]) for n in names}
        report[name] = {"n": len(rows), "always_claimant": sum(1 for y, _, _ in rows if y) / len(rows), "rules": results}
        print(f"\nPART 2, {name} (n={len(rows)}, always-claimant {100 * report[name]['always_claimant']:.1f}%)")
        print(f"  {'rule':<62}{'acc':>7}{'macroF1':>9}{'resp rec':>9}{'coverage':>9}")
        for n, s in results.items():
            rr = s.get("respondent_recall")
            print(f"  {n:<62}{100 * s['accuracy']:>6.1f}%{s['macro_f1']:>9.3f}{('   n/a' if rr is None else f'{100 * rr:>8.0f}%')}{100 * s['coverage']:>8.0f}%")
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
