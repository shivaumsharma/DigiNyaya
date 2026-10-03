"""Full evaluation matrix for the real-judgment benchmark: confusion matrix,
precision/recall/F1, and per-category breakdowns -- not just the 3-way
verdict (match/partial/mismatch) scripts/judge_real_outcomes.py reports.

Treats "did the claimant prevail?" as a binary classification task:
  - predicted label  = claimant_prevailed_ai   (what DigiNyaya's pipeline decided)
  - ground truth      = claimant_prevailed_real (what the real court actually decided)
Both come straight from scripts/judge_real_outcomes.py's judge() output,
persisted per-case since <commit adding this>. Cases judged BEFORE that
change don't have these two fields yet (only the derived "verdict" string) --
this script reports how many cases it could and couldn't include, rather
than silently dropping them.

Run (from backend/): python -m scripts.compute_evaluation_matrix
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
IN_PATH = _DATA_DIR / "real_judgment_verdict_comparison.json"
OUT_PATH = _DATA_DIR / "evaluation_matrix_report.json"


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall and (precision + recall)) else None
    return {
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "support_positive_actual": tp + fn,
    }


def main() -> int:
    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    n_total = len(data)

    usable = [
        d for d in data
        if d.get("verdict") != "judge_failed"
        and d.get("claimant_prevailed_ai") is not None
        and d.get("claimant_prevailed_real") is not None
    ]
    n_missing_fields = sum(
        1 for d in data
        if d.get("verdict") != "judge_failed"
        and (d.get("claimant_prevailed_ai") is None or d.get("claimant_prevailed_real") is None)
    )
    n_judge_failed = sum(1 for d in data if d.get("verdict") == "judge_failed")

    # ---- 3-way verdict distribution (matches judge_real_outcomes.py's own
    # summary -- included here so this report is self-contained) ----
    verdict_counts = Counter(d["verdict"] for d in data)
    full_match = verdict_counts.get("match", 0)
    partial = verdict_counts.get("partial", 0)
    mismatch = verdict_counts.get("mismatch", 0)
    n_verdicted = full_match + partial + mismatch

    # ---- binary confusion matrix: claimant_prevailed_ai (predicted) vs
    # claimant_prevailed_real (actual) ----
    tp = fp = tn = fn = 0
    for d in usable:
        pred = bool(d["claimant_prevailed_ai"])
        actual = bool(d["claimant_prevailed_real"])
        if pred and actual:
            tp += 1
        elif pred and not actual:
            fp += 1
        elif not pred and actual:
            fn += 1
        else:
            tn += 1

    n_usable = len(usable)
    directional_accuracy = (tp + tn) / n_usable if n_usable else None

    claimant_class = _prf(tp, fp, fn)
    # "Respondent prevails / dismissed" as the positive class is the mirror
    # confusion matrix (swap tp<->tn, fp<->fn).
    respondent_class = _prf(tn, fn, fp)
    macro_precision = None
    macro_recall = None
    macro_f1 = None
    if claimant_class["precision"] is not None and respondent_class["precision"] is not None:
        macro_precision = round((claimant_class["precision"] + respondent_class["precision"]) / 2, 4)
        macro_recall = round((claimant_class["recall"] + respondent_class["recall"]) / 2, 4)
        macro_f1 = round((claimant_class["f1"] + respondent_class["f1"]) / 2, 4)

    # ---- relief-similarity-conditioned full match, among direction-correct
    # cases (mirrors how "match" vs "partial" is actually computed) ----
    direction_correct = [d for d in usable if bool(d["claimant_prevailed_ai"]) == bool(d["claimant_prevailed_real"])]
    relief_similar = sum(1 for d in direction_correct if d.get("relief_similarity") == "similar")

    # ---- per-category breakdown ----
    by_cat = defaultdict(lambda: {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "match": 0, "partial": 0, "mismatch": 0, "n": 0})
    for d in data:
        cat = d["category"]
        by_cat[cat]["n"] += 1
        v = d.get("verdict")
        if v in ("match", "partial", "mismatch"):
            by_cat[cat][v] += 1
    for d in usable:
        cat = d["category"]
        pred = bool(d["claimant_prevailed_ai"])
        actual = bool(d["claimant_prevailed_real"])
        if pred and actual:
            by_cat[cat]["tp"] += 1
        elif pred and not actual:
            by_cat[cat]["fp"] += 1
        elif not pred and actual:
            by_cat[cat]["fn"] += 1
        else:
            by_cat[cat]["tn"] += 1

    per_category = {}
    for cat, c in sorted(by_cat.items(), key=lambda kv: -kv[1]["n"]):
        prf = _prf(c["tp"], c["fp"], c["fn"])
        n_verdicted_cat = c["match"] + c["partial"] + c["mismatch"]
        per_category[cat] = {
            "n_total": c["n"],
            "full_match_rate": round(c["match"] / n_verdicted_cat, 4) if n_verdicted_cat else None,
            "ruling_only_rate": round((c["match"] + c["partial"]) / n_verdicted_cat, 4) if n_verdicted_cat else None,
            "confusion": {"tp": c["tp"], "fp": c["fp"], "tn": c["tn"], "fn": c["fn"]},
            "claimant_class_precision": prf["precision"],
            "claimant_class_recall": prf["recall"],
            "claimant_class_f1": prf["f1"],
        }

    report = {
        "n_cases_in_file": n_total,
        "n_verdicted": n_verdicted,
        "n_judge_failed": n_judge_failed,
        "n_usable_for_confusion_matrix": n_usable,
        "n_missing_prevailed_fields_legacy_entries": n_missing_fields,
        "three_way_verdict": {
            "full_match": full_match,
            "partial": partial,
            "mismatch": mismatch,
            "full_match_rate": round(full_match / n_verdicted, 4) if n_verdicted else None,
            "ruling_only_rate": round((full_match + partial) / n_verdicted, 4) if n_verdicted else None,
        },
        "confusion_matrix_claimant_prevails": {
            "true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn,
            "directional_accuracy": round(directional_accuracy, 4) if directional_accuracy is not None else None,
        },
        "claimant_prevails_class_metrics": claimant_class,
        "respondent_prevails_class_metrics": respondent_class,
        "macro_averaged": {"precision": macro_precision, "recall": macro_recall, "f1": macro_f1},
        "relief_similarity_given_direction_correct": {
            "n_direction_correct": len(direction_correct),
            "n_relief_also_similar": relief_similar,
            "rate": round(relief_similar / len(direction_correct), 4) if direction_correct else None,
        },
        "per_category": per_category,
    }
    print(json.dumps(report, indent=2))
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
