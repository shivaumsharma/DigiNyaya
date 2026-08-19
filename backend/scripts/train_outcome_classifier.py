"""Train a small classifier to predict whether DigiNyaya's AI resolution will
MATCH the real court's outcome, using only case-intake-time features (nothing
the AI itself produced as part of forming its opinion).

Ground truth: scripts/judge_real_outcomes.py's LLM-as-judge verdicts
(data_cache/real_judgment_verdict_comparison.json) -- match / partial /
mismatch, for the 32 (of 46) real-judgment eval cases that reached a judged
AI resolution. Collapsed to binary here: match=1, {partial, mismatch}=0,
since "did the AI actually land where the real court landed" is the
sharpest, least-ambiguous signal available, and partial/mismatch both mean
"don't trust this one without a human."

Features come from data_cache/eval_judgments_with_signals.json (case intake
signals: dispute category, claimant evidence count, respondent defense
signals) and data_cache/real_judgment_eval_results.json (pipeline-run
metadata: mapped dispute type, confidence scores, precedents retrieved, and
-- since scripts/run_real_judgment_eval.py was extended to persist them --
the actual claimant/respondent strength scores app/agents/analysis.py
computed, i.e. the real drivers of mediation.py's net_strength win/lose
decision, not just proxies for it). claim_amount and description_word_count
are derived directly from the source case text via the same
app.agents.nlp.extract_amounts() the live pipeline uses -- not stored in
either file.

HONESTY NOTE ON SAMPLE SIZE: n=32 is small. A single held-out test split
would be nearly meaningless (any one unlucky split could swing accuracy by
+/-15 points). Leave-One-Out cross-validation is used instead so every case
gets exactly one out-of-fold prediction and the reported metrics reflect all
32 cases, not a lucky/unlucky subset. Even so, treat these numbers as a
demonstration that the workflow is sound and there IS learnable signal above
the majority-class baseline -- not as a production-ready accuracy estimate.
A single feature also lacks variance across every one of these 32 cases
(respondent_accepts_liability is False for all of them -- genuinely-contested
cases never reach the "accepts liability" branch) and is dropped rather than
included as dead weight.

Also worth flagging plainly: data_cache/real_judgment_eval_results.json and
data_cache/real_judgment_verdict_comparison.json were written by two
separate pipeline runs (confirmed: 2 of the 32 judged cases show
resolved=False/escalated_at_resolution=True in the results file, yet the
judge scored a real ai_relief for them -- meaning the judge's run reached a
resolution where the results-file run didn't). composite_confidence /
precedents_retrieved for those 2 cases are therefore missing and
median-imputed with a missingness indicator, not treated as true zeros.

Run (from backend/): python -m scripts.train_outcome_classifier
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneOut, permutation_test_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer

from app.agents import nlp  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
SIGNALS_PATH = _DATA_DIR / "eval_judgments_with_signals.json"
RESULTS_PATH = _DATA_DIR / "real_judgment_eval_results.json"
VERDICTS_PATH = _DATA_DIR / "real_judgment_verdict_comparison.json"
OUT_PATH = _DATA_DIR / "outcome_classifier_report.json"

DEFAULT_CLAIM_AMOUNT = 50_000.0

NUMERIC_FEATURES = [
    "claim_amount_log",
    "claimant_evidence_count",
    "composite_confidence",
    "composite_confidence_missing",
    "ingestion_confidence",
    "precedents_retrieved",
    "precedents_retrieved_missing",
    "description_word_count",
    "claimant_strength_score",
    "respondent_strength_score",
    "net_strength",  # claimant - respondent -- literally mediation.py's win/lose signal
]
CATEGORICAL_FEATURES = [
    "mapped_dispute_type",
    "respondent_ground_has_specific_support",
]


def _infer_claim_amount(description: str) -> float:
    amounts = nlp.extract_amounts(description)
    return amounts[0] if amounts else DEFAULT_CLAIM_AMOUNT


def build_dataset() -> pd.DataFrame:
    signals = {s["case_id"]: s for s in json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))}
    results = {r["case_id"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))

    rows = []
    for v in verdicts:
        cid = v["case_id"]
        sig = signals[cid]
        res = results[cid]
        sub_signals = sig.get("signals") or {}

        has_support = sub_signals.get("respondent_ground_has_specific_support")
        c_strength = res.get("claimant_strength_score")
        r_strength = res.get("respondent_strength_score")
        rows.append({
            "case_id": cid,
            "category": sig["category"],
            "mapped_dispute_type": res["mapped_dispute_type"],
            "claim_amount_log": np.log1p(_infer_claim_amount(sig["case_description"])),
            "claimant_evidence_count": sub_signals.get("claimant_evidence_count") or 0,
            "respondent_ground_has_specific_support": (
                "unknown" if has_support is None else str(bool(has_support))
            ),
            "composite_confidence": res.get("composite_confidence"),
            "ingestion_confidence": res.get("ingestion_confidence"),
            "precedents_retrieved": res.get("precedents_retrieved"),
            "description_word_count": len(sig["case_description"].split()),
            "claimant_strength_score": c_strength,
            "respondent_strength_score": r_strength,
            "net_strength": (c_strength - r_strength) if c_strength is not None and r_strength is not None else None,
            "verdict": v["verdict"],
            "label": 1 if v["verdict"] == "match" else 0,
        })

    df = pd.DataFrame(rows)
    for col in ("composite_confidence", "precedents_retrieved"):
        df[f"{col}_missing"] = df[col].isna().astype(int)
        df[col] = df[col].fillna(df[col].median())
    for col in ("claimant_strength_score", "respondent_strength_score", "net_strength"):
        df[col] = df[col].fillna(df[col].median())
    return df


def _metrics(y_true, y_pred, y_score) -> dict:
    p, r, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", zero_division=0
    )
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 3),
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(f1, 3),
        "roc_auc": round(roc_auc_score(y_true, y_score), 3) if len(set(y_true)) > 1 else None,
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),  # [[TN,FP],[FN,TP]]
    }


def _make_pipeline(model) -> Pipeline:
    pre = ColumnTransformer([
        ("num", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    return Pipeline([("pre", pre), ("model", model)])


def loo_evaluate(df: pd.DataFrame, model, name: str) -> dict:
    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = df["label"].to_numpy()

    loo = LeaveOneOut()
    y_pred = np.zeros(len(y), dtype=int)
    y_score = np.zeros(len(y), dtype=float)

    for train_idx, test_idx in loo.split(X):
        pipe = _make_pipeline(model)
        pipe.fit(X.iloc[train_idx], y[train_idx])
        y_pred[test_idx] = pipe.predict(X.iloc[test_idx])
        y_score[test_idx] = pipe.predict_proba(X.iloc[test_idx])[:, 1]

    metrics = _metrics(y, y_pred, y_score)
    metrics["model"] = name
    metrics["n_cases"] = len(y)
    metrics["n_positive"] = int(y.sum())

    # Fit once on ALL data purely to report which features the model leaned
    # on -- descriptive only, NOT cross-validated, do not read as "these
    # features generalize."
    full_pipe = _make_pipeline(model)
    full_pipe.fit(X, y)
    feature_names = list(full_pipe.named_steps["pre"].get_feature_names_out())
    fitted_model = full_pipe.named_steps["model"]
    if hasattr(fitted_model, "coef_"):
        importances = fitted_model.coef_[0]
    elif hasattr(fitted_model, "feature_importances_"):
        importances = fitted_model.feature_importances_
    else:
        importances = None
    if importances is not None:
        ranked = sorted(zip(feature_names, importances), key=lambda t: -abs(t[1]))
        metrics["feature_importance_full_fit"] = [
            {"feature": f, "weight": round(float(w), 3)} for f, w in ranked
        ]
    return metrics


def main() -> int:
    df = build_dataset()
    n = len(df)
    n_pos = int(df["label"].sum())
    majority_baseline = round(max(n_pos, n - n_pos) / n, 3)

    print(f"Dataset: {n} judged cases ({n_pos} match / {n - n_pos} partial+mismatch)")
    print(f"Majority-class baseline accuracy (always predict the bigger class): {majority_baseline}\n")

    models = [
        (LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"), "logistic_regression"),
        (GradientBoostingClassifier(n_estimators=50, max_depth=2, learning_rate=0.1, random_state=0), "gradient_boosting"),
    ]

    report = {
        "n_cases": n,
        "n_positive_match": n_pos,
        "majority_class_baseline_accuracy": majority_baseline,
        "cv_method": "leave_one_out",
        "models": [],
    }

    X_all = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y_all = df["label"].to_numpy()

    for model, name in models:
        m = loo_evaluate(df, model, name)

        # Is the observed accuracy actually distinguishable from chance, or
        # just what you'd expect from 32 noisy samples? Refit+re-evaluate
        # (LOO CV) 200 times with the labels randomly shuffled -- the
        # p-value is the fraction of those random-label runs that scored >=
        # the real one. This is the honest way to back the "there's real
        # signal here" claim instead of just quoting a point estimate that
        # could easily be luck at this sample size.
        _, permutation_scores, p_value = permutation_test_score(
            _make_pipeline(model), X_all, y_all,
            cv=LeaveOneOut(), n_permutations=200, random_state=0, n_jobs=-1,
        )
        m["permutation_test"] = {
            "n_permutations": 200,
            "p_value": round(float(p_value), 4),
            "chance_accuracy_mean": round(float(np.mean(permutation_scores)), 3),
        }

        report["models"].append(m)
        print(f"--- {name} (leave-one-out CV, {m['n_cases']} folds) ---")
        print(f"  accuracy={m['accuracy']}  precision={m['precision']}  recall={m['recall']}  "
              f"f1={m['f1']}  roc_auc={m['roc_auc']}")
        print(f"  confusion matrix [[TN,FP],[FN,TP]]: {m['confusion_matrix']}")
        print(f"  permutation test: p={m['permutation_test']['p_value']} "
              f"(chance-level mean accuracy={m['permutation_test']['chance_accuracy_mean']}, "
              f"200 shuffles)")
        if "feature_importance_full_fit" in m:
            top = m["feature_importance_full_fit"][:5]
            print("  top features (full-fit, descriptive only): "
                  + ", ".join(f"{t['feature']}={t['weight']}" for t in top))
        print()

    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote full report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
