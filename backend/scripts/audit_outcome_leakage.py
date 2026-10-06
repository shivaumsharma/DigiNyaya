"""Audit the real-judgment eval corpus for outcome leakage.

Each eval case's `case_description` was written by an LLM that READ the whole judgment (see
scripts/source_free_judgments.py: "NOT a summary of the judgment itself", but the model saw the result).
If the description leaks the outcome, every downstream number (winner accuracy, amount match) is inflated
in a way no pipeline improvement explains. ILDC (Malik et al., ACL 2021) deleted the decision sections and
anonymised judge names for exactly this reason: their legal experts said a judge's identity can be a strong
indicator of the outcome. This script measures four things, none of which need an LLM:

  1. OUTCOME LANGUAGE   share of descriptions containing STRONG result-revealing phrasing ("was decreed",
                        "the court ordered", "partly allowed", ...), by category; ambiguous WEAK phrasing
                        ("is entitled to", "in favour of the plaintiff") is reported separately, not as leakage.
  2. JUDGE / COURT      share of descriptions that name a judge or presiding officer (the sourcing prompt
                        explicitly allows this), by category.
  3. AMOUNT ECHO        share where a rupee figure from `expected_outcome` also appears verbatim in the
                        description. Not leakage by itself (a claim amount often equals the award), but it
                        inflates "relief within +/-20%" when the description simply states the figure.
  4. DESCRIPTION-ONLY   if data_cache/real_judgment_verdict_comparison_v2.json (or the older file) exists, a cross-validated
     PREDICTABILITY     TF-IDF logistic regression that sees ONLY the description and predicts whether the
                        claimant prevailed in the real judgment, against the majority-class rate. Accuracy
                        well above the majority rate means the text alone gives the answer away.

Flagged rows go to a CSV for hand review. The thresholds for "worrying" are printed, not enforced: this is
a measuring instrument, not a gate.

Run (from backend/):  python -m scripts.audit_outcome_leakage
  --dataset PATH    eval dataset JSON (default: the one the eval scripts use)
  --verdicts PATH   verdict comparison JSON (default: data_cache/real_judgment_verdict_comparison.json)
  --flagged PATH    where to write flagged rows (default: data_cache/leakage_flagged.csv)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
DEFAULT_VERDICTS = (DATA / "real_judgment_verdict_comparison_v2.json"
                    if (DATA / "real_judgment_verdict_comparison_v2.json").exists()
                    else DATA / "real_judgment_verdict_comparison.json")

# Result-revealing phrasing, in two tiers because precision differs enormously. On the real corpus the first
# version of this lexicon flagged 35.7% of descriptions, but 98% of that came from two broad patterns
# ("is entitled to ...", "in favour of the plaintiff") that appear all the time in neutral statements of the
# facts or the claim. So:
#   STRONG  phrasing that almost only appears when stating what the court decided
#   WEAK    phrasing that is ambiguous (claims, procedural history, facts) -- reported, but not treated as leakage
# A description may legitimately say "the claimant seeks a decree", so bare "decree" is in neither tier.
STRONG_PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(rx, re.IGNORECASE)
    for name, rx in {
        "was decreed": r"\b(?:was|is|stands?|been)\s+decreed\b|\bdecreed\s+(?:the|in\s+favou?r)",
        "allowed": r"\b(?:was|is|stands?|been)\s+(?:partly\s+|partially\s+)?allowed\b|\b(?:partly|partially)\s+allowed\b",
        "court decreed/ordered": r"\b(?:the\s+)?(?:court|tribunal|forum|commission|judge)\s+(?:decreed|awarded|ordered|directed|concluded|ruled)\b",
        "judgment pronounced": r"\bjudg(?:e)?ment\s+(?:was\s+)?(?:passed|pronounced|rendered|entered)\b",
        "decree in favour": r"\b(?:decree|judg(?:e)?ment)\s+(?:is\s+|was\s+)?(?:passed\s+|granted\s+)?in\s+favou?r\s+of\b",
    }.items()
}
WEAK_PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(rx, re.IGNORECASE)
    for name, rx in {
        "dismissed": r"\b(?:was|is|stands?|been|were)\s+dismissed\b|\b(?:suit|appeal|complaint|claim|petition)\s+(?:is\s+|was\s+|stands\s+)?dismissed\b",
        "court held/found": r"\b(?:the\s+)?(?:court|tribunal|forum|commission|judge)\s+(?:held|found)\b",
        "entitled/liable": r"\b(?:is|was|are|were)\s+(?:held\s+)?(?:entitled\s+to|liable\s+to\s+pay|directed\s+to\s+pay|ordered\s+to\s+pay)\b",
        "in favour of party": r"\bin\s+favou?r\s+of\s+the\s+(?:plaintiff|claimant|complainant|defendant|respondent|tenant|landlord)\b",
        "accordingly": r"\baccordingly\b|\bthe\s+(?:final\s+)?(?:order|decision|verdict)\s+(?:was|is)\b",
    }.items()
}
OUTCOME_PATTERNS = {**STRONG_PATTERNS, **WEAK_PATTERNS}  # kept for callers that want every pattern

# Names a judge or presiding officer (the sourcing prompt says "You MAY name the court and any judge").
_TITLE = r"(?i:smt|shri|sri|mr|ms|dr)"
JUDGE_PATTERN = re.compile(
    "|".join([
        # "presided over by Smt. K. Pooja", "presiding by Justice ..."
        r"(?i:presid(?:ed|ing)\s+(?:over\s+)?by)\s+(?:(?i:smt|shri|sri|mr|ms|dr|hon'?ble|justice|judge)\b|[A-Z])",
        # "Hon'ble Justice Rao", "Hon'ble Mr. Justice Rao"
        r"(?i:hon'?ble)\s+(?:" + _TITLE + r"\.?\s+)?(?i:justice|judge)\b",
        # "Justice Sharma" (but not "access to Justice Act", "rule of Justice ...")
        r"(?<!to )(?<!of )(?<!for )(?i:\bjustice)\s+[A-Z][a-z]+",
        # "Judge Sharma", "Magistrate Smt. Rao" (but not "Magistrate First Class", "Judge Court")
        r"(?i:\b(?:judge|magistrate))\s+(?:" + _TITLE + r"\.?\s+)?(?!First\b|Second\b|Third\b|Class\b|Court\b|Grade\b)[A-Z]\w*",
        # "Smt. K. Pooja, Judge"
        _TITLE + r"\.?\s+[A-Z]\.?\s+[A-Z][a-z]+\s*,?\s*(?i:presiding|judge)",
    ])
)

_AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def amounts(text: str) -> set[int]:
    """Rupee figures >= 1000 mentioned in `text`, as ints (so 2,00,000 == 200000 == 2,00,000.00)."""
    out = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        try:
            v = int(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
        if v >= 1000:
            out.add(v)
    return out


def audit_text(case: dict) -> dict:
    desc = case.get("case_description") or ""
    outcome = case.get("expected_outcome") or ""
    strong = {name: m.group(0) for name, rx in STRONG_PATTERNS.items() if (m := rx.search(desc))}
    weak = {name: m.group(0) for name, rx in WEAK_PATTERNS.items() if (m := rx.search(desc))}
    echo = amounts(desc) & amounts(outcome)
    return {
        "case_id": case.get("case_id"),
        "category": case.get("category") or "unknown",
        "outcome_language": list(strong),
        "weak_language": list(weak),
        "matched_text": {**strong, **weak},
        "names_judge": bool(JUDGE_PATTERN.search(desc)),
        "amount_echo": sorted(echo),
    }


def rate(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0"


def _macro_f1(y_true: list[int], y_pred: list[int]) -> tuple[float, float, float]:
    """(macro-F1, recall of class 1, recall of class 0)."""
    f1s, recalls = [], []
    for cls in (1, 0):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p == cls)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != cls and p == cls)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p != cls)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
        recalls.append(rec)
    return sum(f1s) / 2, recalls[0], recalls[1]


def description_only_cv(cases: list[dict], verdicts: dict[str, dict], folds: int = 5, seed: int = 7) -> dict | None:
    """Cross-validated performance of a description-only classifier of claimant_prevailed_real vs the majority rate,
    and -- where the verdict rows carry the pipeline's own call (claimant_prevailed_ai) -- a paired comparison with
    the pipeline on exactly the same cases. None when there is not enough labelled data or scikit-learn is missing."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.pipeline import make_pipeline
    except ImportError:
        return None
    xs, ys, ai = [], [], []
    for c in cases:
        v = verdicts.get(c.get("case_id"))
        if v is None or v.get("claimant_prevailed_real") is None or not c.get("case_description"):
            continue
        xs.append(c["case_description"])
        ys.append(int(bool(v["claimant_prevailed_real"])))
        ai.append(None if v.get("claimant_prevailed_ai") is None else int(bool(v["claimant_prevailed_ai"])))
    if len(xs) < 50 or len(set(ys)) < 2 or min(Counter(ys).values()) < folds:
        return None
    pipe = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=50000),
                         LogisticRegression(max_iter=1000, C=1.0))
    pred = [int(p) for p in cross_val_predict(pipe, xs, ys, cv=StratifiedKFold(folds, shuffle=True, random_state=seed))]
    acc = sum(int(p == y) for p, y in zip(pred, ys)) / len(ys)
    majority = max(Counter(ys).values()) / len(ys)
    f1, rec_claimant, rec_respondent = _macro_f1(ys, pred)
    out = {"n": len(ys), "cv_accuracy": acc, "majority_rate": majority, "lift_over_majority": acc - majority,
           "macro_f1": f1, "recall_claimant_wins": rec_claimant, "recall_respondent_wins": rec_respondent}
    paired = [(y, p, a) for y, p, a in zip(ys, pred, ai) if a is not None]
    if len(paired) >= 50:
        from scripts.selective_guarantee import mcnemar_exact
        ys_p, ai_p = [t[0] for t in paired], [t[2] for t in paired]
        text_right_pipe_wrong = sum(1 for y, t, a in paired if t == y and a != y)
        pipe_right_text_wrong = sum(1 for y, t, a in paired if a == y and t != y)
        pf1, prec_c, prec_r = _macro_f1(ys_p, ai_p)
        out["pipeline"] = {
            "n": len(paired), "accuracy": sum(int(a == y) for y, _, a in paired) / len(paired), "macro_f1": pf1,
            "recall_claimant_wins": prec_c, "recall_respondent_wins": prec_r,
            "text_right_pipeline_wrong": text_right_pipe_wrong, "pipeline_right_text_wrong": pipe_right_text_wrong,
            "mcnemar_p": mcnemar_exact(text_right_pipe_wrong, pipe_right_text_wrong),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset")
    ap.add_argument("--verdicts", default=str(DEFAULT_VERDICTS))
    ap.add_argument("--flagged", default=str(DATA / "leakage_flagged.csv"))
    ap.add_argument("--include-weak", action="store_true", help="also write rows that only have WEAK phrasing (large)")
    args = ap.parse_args()

    if args.dataset:
        dataset_path = Path(args.dataset)
    else:
        from scripts.run_real_judgment_eval import DATASET_PATH as dataset_path
    if not Path(dataset_path).exists():
        print(f"Not found: {dataset_path}")
        return 1
    cases = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    rows = [audit_text(c) for c in cases]
    n = len(rows)
    print(f"Audited {n} cases from {dataset_path}\n")

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    print("1a. STRONG outcome language in case_description (phrasing that states what the court decided)")
    leaked = [r for r in rows if r["outcome_language"]]
    print(f"   overall: {rate(len(leaked), n)}")
    for name, k in Counter(h for r in rows for h in r["outcome_language"]).most_common():
        print(f"     {name:22s} {rate(k, n)}")
    for cat, rs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"   {cat:34s} {rate(sum(1 for r in rs if r['outcome_language']), len(rs))}")
    print("\n1b. WEAK / ambiguous language (often just the facts or the claim; reported, NOT counted as leakage)")
    weak_rows = [r for r in rows if r["weak_language"]]
    print(f"   overall: {rate(len(weak_rows), n)}")
    for name, k in Counter(h for r in rows for h in r["weak_language"]).most_common():
        print(f"     {name:22s} {rate(k, n)}")

    print("\n2. JUDGE / PRESIDING OFFICER named in case_description")
    judged = [r for r in rows if r["names_judge"]]
    print(f"   overall: {rate(len(judged), n)}")
    for cat, rs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"   {cat:34s} {rate(sum(1 for r in rs if r['names_judge']), len(rs))}")

    print("\n3. AMOUNT ECHO (an outcome rupee figure also stated in the description)")
    echoed = [r for r in rows if r["amount_echo"]]
    with_amount = sum(1 for c in cases if amounts(c.get("expected_outcome") or ""))
    print(f"   {rate(len(echoed), with_amount)} of cases whose outcome states a figure")

    print("\n4. DESCRIPTION-ONLY PREDICTABILITY of whether the claimant prevailed")
    verdicts_path = Path(args.verdicts)
    cv = None
    if verdicts_path.exists():
        verdicts = {v["case_id"]: v for v in json.loads(verdicts_path.read_text(encoding="utf-8"))}
        cv = description_only_cv(cases, verdicts)
    if cv is None:
        print(f"   skipped (needs {verdicts_path.name}, >= 50 labelled cases in both classes, and scikit-learn)")
    else:
        print(f"   n={cv['n']}  CV accuracy={100 * cv['cv_accuracy']:.1f}%  majority rate={100 * cv['majority_rate']:.1f}%  "
              f"lift={100 * cv['lift_over_majority']:+.1f} points")
        print(f"   macro-F1={cv['macro_f1']:.3f}  recall when claimant wins={100 * cv['recall_claimant_wins']:.1f}%  "
              f"recall when respondent wins={100 * cv['recall_respondent_wins']:.1f}%")
        print("   A lift of more than a few points means the description text alone predicts the real outcome.")
        pl = cv.get("pipeline")
        if pl:
            print(f"\n   SAME {pl['n']} cases, DigiNyaya's pipeline vs this text-only classifier (winner = did the claimant prevail):")
            print(f"     pipeline  accuracy={100 * pl['accuracy']:.1f}%  macro-F1={pl['macro_f1']:.3f}  "
                  f"recall claimant-wins={100 * pl['recall_claimant_wins']:.1f}%  respondent-wins={100 * pl['recall_respondent_wins']:.1f}%")
            print(f"     text-only accuracy={100 * cv['cv_accuracy']:.1f}%  macro-F1={cv['macro_f1']:.3f}  "
                  f"recall claimant-wins={100 * cv['recall_claimant_wins']:.1f}%  respondent-wins={100 * cv['recall_respondent_wins']:.1f}%")
            print(f"     text right & pipeline wrong: {pl['text_right_pipeline_wrong']}   pipeline right & text wrong: "
                  f"{pl['pipeline_right_text_wrong']}   McNemar exact p = {pl['mcnemar_p']:.3g}")
            print("     Report the pipeline against this baseline: it is cheap, needs no LLM, and reviewers will ask.")

        strong_ids = {r["case_id"] for r in rows if r["outcome_language"] or r["names_judge"]}
        clean_cases = [c for c in cases if c.get("case_id") not in strong_ids]
        cv_clean = description_only_cv(clean_cases, verdicts)
        print(f"\n   ABLATION: drop the {len(cases) - len(clean_cases)} descriptions with STRONG outcome language or a named judge, retrain:")
        if cv_clean is None:
            print("     skipped (too few labelled cases left)")
        else:
            print(f"     n={cv_clean['n']}  CV accuracy={100 * cv_clean['cv_accuracy']:.1f}%  majority rate={100 * cv_clean['majority_rate']:.1f}%  "
                  f"lift={100 * cv_clean['lift_over_majority']:+.1f} points  macro-F1={cv_clean['macro_f1']:.3f}")
            drop = cv["lift_over_majority"] - cv_clean["lift_over_majority"]
            print(f"     The lift moved by {100 * drop:+.1f} points. If it barely moves, the baseline's edge is NOT coming from "
                  "explicit result phrasing; if it collapses, those descriptions were giving the answer away.")

    flagged = [r for r in rows if r["outcome_language"] or r["names_judge"] or (args.include_weak and r["weak_language"])]
    if flagged:
        by_id = {c.get("case_id"): c for c in cases}
        Path(args.flagged).parent.mkdir(parents=True, exist_ok=True)
        with open(args.flagged, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["case_id", "category", "outcome_language", "weak_language", "matched_text", "names_judge", "case_description"])
            for r in flagged:
                w.writerow([r["case_id"], r["category"], "; ".join(r["outcome_language"]), "; ".join(r["weak_language"]),
                            " | ".join(f"{k}: {v}" for k, v in r["matched_text"].items()), r["names_judge"],
                            by_id.get(r["case_id"], {}).get("case_description", "")])
        print(f"\nWrote {len(flagged)} flagged row(s) for hand review -> {args.flagged}")
    print("\nRead this as: a high rate in 1 or 2 means the descriptions need regenerating or scrubbing before any "
          "accuracy number is trusted; 3 and 4 tell you how much of a headline could be the text giving the answer away.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
