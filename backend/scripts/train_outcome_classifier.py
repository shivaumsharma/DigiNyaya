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
  --split all       (default) every judged case -- unchanged behavior
  --split explore    the exploration bucket only -- iterate freely here
  --split confirm    the locked confirmation bucket -- run exactly once,
                      refuses to overwrite an existing confirmation report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
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


# --------------------------------------------------------------------------- #
# Exploration / confirmation split (locked, deterministic, case_id-keyed)
# --------------------------------------------------------------------------- #
# The standard fix for the garden-of-forking-paths problem: iterate freely
# (new features, model swaps, label fixes) on the EXPLORE bucket only, then
# run the permutation test exactly once on the CONFIRM bucket and report
# whatever comes out -- good or bad. See --split below.
#
# The split is a pure function of case_id (sha256 -> int mod 1000), not a
# stored list or a random.seed() draw. Two consequences that matter:
#   1. It can't be silently re-rolled to go looking for a luckier split --
#      changing the outcome requires visibly editing CONFIRMATION_FRACTION or
#      this function, which shows up in a diff.
#   2. An existing case's bucket never changes when new cases are added later
#      (lever #1: growing the dataset) -- a case_id that hashed to "explore"
#      stays "explore" forever; new case_ids just land wherever their own
#      hash puts them. The confirm set only grows, never gets reshuffled.
CONFIRMATION_FRACTION = 0.35


def _split_bucket(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 1000
    return "confirm" if bucket < int(1000 * CONFIRMATION_FRACTION) else "explore"


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
            "split": _split_bucket(cid),
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


class _BlendedClassifier(BaseEstimator, ClassifierMixin):
    """Averages the predicted probabilities of two already-built pipelines
    (each with its own preprocessing) -- a plain 50/50 soft-voting blend,
    nothing learned about how to weight them.

    Motivated by something actually observed across this project's own
    re-runs, not by theory: logistic_regression and gradient_boosting have
    flip-flopped on which one clears the permutation-test significance bar
    across several different data states this session, while the other
    stayed non-significant. Two models disagreeing on WHICH cases they get
    right is the textbook situation where averaging tends to be more
    STABLE than trusting either alone -- worth testing directly rather than
    assuming. Takes fully-built pipelines (not bare estimators) so each
    side keeps its own already-tuned preprocessing; nothing here needs
    _make_pipeline() wrapping."""

    def __init__(self, pipeline_a: Pipeline, pipeline_b: Pipeline) -> None:
        self.pipeline_a = pipeline_a
        self.pipeline_b = pipeline_b

    def fit(self, X, y):
        self.pipeline_a_ = clone(self.pipeline_a).fit(X, y)
        self.pipeline_b_ = clone(self.pipeline_b).fit(X, y)
        self.classes_ = self.pipeline_a_.classes_
        return self

    def predict_proba(self, X):
        return (self.pipeline_a_.predict_proba(X) + self.pipeline_b_.predict_proba(X)) / 2.0

    def predict(self, X):
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]


def loo_evaluate(df: pd.DataFrame, model, name: str, *, prebuilt: bool = False) -> dict:
    """prebuilt=True means `model` is already a complete, fittable estimator
    (e.g. _BlendedClassifier, which wraps its own two preprocessing
    pipelines internally) -- skip the _make_pipeline() wrapping that bare
    sklearn estimators (LogisticRegression, GradientBoostingClassifier,
    RandomForestClassifier) need."""
    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = df["label"].to_numpy()

    loo = LeaveOneOut()
    y_pred = np.zeros(len(y), dtype=int)
    y_score = np.zeros(len(y), dtype=float)

    for train_idx, test_idx in loo.split(X):
        pipe = model if prebuilt else _make_pipeline(model)
        pipe.fit(X.iloc[train_idx], y[train_idx])
        y_pred[test_idx] = pipe.predict(X.iloc[test_idx])
        y_score[test_idx] = pipe.predict_proba(X.iloc[test_idx])[:, 1]

    metrics = _metrics(y, y_pred, y_score)
    metrics["model"] = name
    metrics["n_cases"] = len(y)
    metrics["n_positive"] = int(y.sum())

    if prebuilt:
        # A blend of two models' probabilities has no single coherent
        # feature-importance ranking to report -- skip rather than fake one.
        return metrics

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


def main(argv: list[str] | None = None) -> int:
    """argv defaults to None, which makes argparse read sys.argv[1:] as
    normal for a direct CLI invocation. Pass an explicit list (e.g. [] for
    all-defaults) when calling this as a function from another script --
    scripts/judge_real_outcomes.py auto-chains into this after every run to
    keep the classifier's ground truth fresh, and without this parameter it
    silently inherited THAT script's own argv (--live-llm, --workers N),
    which argparse then rejected outright since this parser doesn't define
    them -- confirmed live, the auto-chain errored out every single time
    judge_real_outcomes.py was run with any of its own CLI flags."""
    ap = argparse.ArgumentParser(description="Train/evaluate the outcome classifier.")
    ap.add_argument(
        "--split", choices=["all", "explore", "confirm"], default="all",
        help=(
            "'all' (default): every judged case, written to outcome_classifier_report.json "
            "-- unchanged from before, so existing numbers stay comparable. "
            "'explore': iterate freely here (new features, model swaps, label fixes) -- "
            "writes outcome_classifier_explore_report.json, safe to re-run as often as you "
            "like. 'confirm': the locked held-out set -- writes "
            "outcome_classifier_confirmation_report.json and REFUSES to overwrite an "
            "existing one (see --force-confirm-rerun). Run this exactly once per real "
            "change, after you're done iterating on 'explore', and report whatever comes out."
        ),
    )
    ap.add_argument(
        "--force-confirm-rerun", action="store_true",
        help="Allow overwriting an existing confirmation report. Only use this for a "
             "genuine reason (e.g. the dataset grew and you're deliberately re-locking a "
             "bigger confirmation run) -- rerunning confirm to chase a better p-value is "
             "exactly the p-hacking failure mode this split exists to prevent.",
    )
    ap.add_argument(
        "--extra-models", action="store_true",
        help="Also evaluate random_forest and a blended logistic+gradient-boosting "
             "ensemble alongside the three default models (logistic_regression, "
             "gradient_boosting, logistic_regression_l1). Model-selection "
             "experimentation -- intended for --split explore only.",
    )
    args = ap.parse_args(argv)

    full_df = build_dataset()
    bucket_counts = full_df["split"].value_counts().to_dict()
    print(
        f"Locked split (case_id-hash, {int(CONFIRMATION_FRACTION * 100)}% target confirm): "
        f"explore={bucket_counts.get('explore', 0)}  confirm={bucket_counts.get('confirm', 0)}\n"
    )

    if args.split == "explore":
        df = full_df[full_df["split"] == "explore"].reset_index(drop=True)
        out_path = _DATA_DIR / "outcome_classifier_explore_report.json"
    elif args.split == "confirm":
        df = full_df[full_df["split"] == "confirm"].reset_index(drop=True)
        out_path = _DATA_DIR / "outcome_classifier_confirmation_report.json"
        if out_path.exists() and not args.force_confirm_rerun:
            print(
                f"REFUSING to overwrite {out_path} -- it already holds a confirmation "
                "result. Running the confirmation set more than once and keeping the best "
                "result is exactly the p-hacking failure mode this split exists to prevent. "
                "Pass --force-confirm-rerun only if you have a genuine reason (e.g. the "
                "dataset grew and you're deliberately re-locking a bigger confirmation run)."
            )
            return 1
    else:
        df = full_df
        out_path = OUT_PATH

    n = len(df)
    n_pos = int(df["label"].sum())
    majority_baseline = round(max(n_pos, n - n_pos) / n, 3)

    print(f"Dataset ({args.split}): {n} judged cases ({n_pos} match / {n - n_pos} partial+mismatch)")
    print(f"Majority-class baseline accuracy (always predict the bigger class): {majority_baseline}\n")

    # (model_or_pipeline, name, prebuilt) -- prebuilt=True means the entry
    # is already a complete fittable estimator (its own preprocessing
    # included), skip _make_pipeline() wrapping for it.
    logistic_l2 = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    gradient_boosting = GradientBoostingClassifier(n_estimators=50, max_depth=2, learning_rate=0.1, random_state=0)
    # L1 ("Lasso") promoted into the DEFAULT set, not gated behind
    # --extra-models, after the locked --split confirm comparison (5
    # models, n=115) came back with L1 as the clear best result: highest
    # F1 (0.65), highest recall (0.776), and the most significant
    # permutation-test p-value of any model tried (p=0.01) -- beating even
    # random_forest (p=0.03), which had looked like the front-runner on
    # the EXPLORE split alone. That reversal is exactly why this was only
    # promoted after seeing the confirm result, not before: an explore-only
    # recommendation would have picked random_forest and missed this.
    # With 11+ candidate features on ~342 rows, L1's sparsity also shows
    # which features the model can actually afford to keep vs. which it's
    # just fitting noise to -- a different, arguably more useful signal
    # than L2's shrink-everything-a-bit "importance" ranking.
    logistic_l1 = LogisticRegression(
        max_iter=2000, C=0.5, class_weight="balanced", penalty="l1", solver="liblinear",
    )
    models = [
        (logistic_l2, "logistic_regression", False),
        (gradient_boosting, "gradient_boosting", False),
        (logistic_l1, "logistic_regression_l1", False),
    ]
    if args.extra_models:
        models += [
            (
                RandomForestClassifier(
                    n_estimators=100, max_depth=4, min_samples_leaf=5,
                    class_weight="balanced", random_state=0,
                ),
                "random_forest", False,
            ),
            (
                _BlendedClassifier(_make_pipeline(logistic_l2), _make_pipeline(gradient_boosting)),
                "blend_logistic_gradient_boosting", True,
            ),
        ]

    report = {
        "split": args.split,
        "n_cases": n,
        "n_positive_match": n_pos,
        "majority_class_baseline_accuracy": majority_baseline,
        "cv_method": "leave_one_out",
        "models": [],
    }

    X_all = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y_all = df["label"].to_numpy()

    for model, name, prebuilt in models:
        m = loo_evaluate(df, model, name, prebuilt=prebuilt)

        # Is the observed accuracy actually distinguishable from chance, or
        # just what you'd expect from 32 noisy samples? Refit+re-evaluate
        # (LOO CV) 200 times with the labels randomly shuffled -- the
        # p-value is the fraction of those random-label runs that scored >=
        # the real one. This is the honest way to back the "there's real
        # signal here" claim instead of just quoting a point estimate that
        # could easily be luck at this sample size.
        perm_estimator = model if prebuilt else _make_pipeline(model)
        _, permutation_scores, p_value = permutation_test_score(
            perm_estimator, X_all, y_all,
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

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote full report -> {out_path}")
    if args.split == "confirm":
        print(
            "\nThis is the locked confirmation result. Report it as-is -- do not tweak "
            "features/model and re-run --confirm chasing a different number."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
