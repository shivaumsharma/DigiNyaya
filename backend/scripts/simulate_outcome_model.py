"""TEST SIMULATION (not a production build): does a model trained directly
on raw case facts predict the REAL court's outcome better than the current
hand-tuned analysis.py/mediation.py formula does?

WHY THIS IS A DIFFERENT QUESTION than scripts/train_outcome_classifier.py:
that script predicts "did DigiNyaya's drafted resolution match the real
outcome" -- a label about the CURRENT heuristic's own behavior. It has no
ground truth for "did the claimant actually win in reality", which is the
one thing you'd need to know before replacing the heuristic with something
learned. This script extracts that missing label directly from each case's
already-sourced expected_outcome text (real ground truth, never seen by the
scoring formula) for a representative sample, then compares:
  (a) majority-class baseline
  (b) the CURRENT heuristic's own implied prediction: net_strength > 0
      (this is the actual bar a trained replacement has to clear)
  (c) a logistic regression trained on RAW FACTS ONLY (no c_score/r_score
      inputs -- the point is to see if facts alone predict reality better
      than the hand-tuned formula derived from those same facts does)

Same discipline as train_outcome_classifier.py: a locked, deterministic
explore/confirm split (reusing that script's own _split_bucket -- same
hash, so a case_id's bucket is identical across both scripts), LOO-CV, and
a 200-shuffle permutation test on the confirm split only.

SAMPLE, NOT THE FULL CORPUS: this is a quick simulation to decide whether
building the real thing (extracting this label for all ~1,900+ resolved
cases and integrating a fitted model into the pipeline) is worth doing at
all. Extracting the label is a real LLM cost (Sarvam, prepaid) but modest
at sample scale.

Run (from backend/): python -m scripts.simulate_outcome_model --sample 500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score  # noqa: E402
from sklearn.model_selection import LeaveOneOut  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

from app import llm  # noqa: E402
from app.agents import nlp  # noqa: E402
from scripts.train_outcome_classifier import _split_bucket  # noqa: E402  -- reuse the SAME locked split

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
SIGNALS_PATH = _DATA_DIR / "eval_judgments_with_signals.json"
RESULTS_PATH = _DATA_DIR / "real_judgment_eval_results.json"
VERDICTS_PATH = _DATA_DIR / "real_judgment_verdict_comparison.json"
LABELS_PATH = _DATA_DIR / "real_outcome_labels_sample.json"
OUT_PATH = _DATA_DIR / "outcome_model_simulation_report.json"

DEFAULT_CLAIM_AMOUNT = 50_000.0
RANDOM_SEED = 42

NUMERIC_FEATURES = [
    "claim_amount_log",
    "claimant_evidence_count",
    "description_word_count",
    "ingestion_confidence",
]
CATEGORICAL_FEATURES = [
    "mapped_dispute_type",
    "respondent_ground_has_specific_support",
    "respondent_accepts_liability",
    "has_documentary_instrument",
]


def _extract_real_outcome(expected_outcome: str) -> dict | None:
    schema = (
        '{"claimant_prevailed": <true if the court\'s ACTUAL decision grants the claimant/plaintiff '
        "meaningful relief (money, possession, injunction, declaration) in their favor -- false if the "
        "claim/suit was dismissed, or if relief instead went to the respondent/defendant (e.g. a "
        'counter-claim succeeded)>, "relief_ratio": <float 0.0-1.5: roughly how much of what was claimed '
        "was actually granted -- 0.0 if dismissed entirely, 1.0 if granted in full, higher only if "
        "damages/interest meaningfully exceeded the bare principal claimed, null if not determinable "
        'from the text>}'
    )
    prompt = (
        "Read ONLY the court's actual holding/order below. Return JSON only, matching this schema: "
        f"{schema}\n\nCOURT'S ACTUAL OUTCOME:\n{expected_outcome}"
    )
    # max_tokens=600, not a small budget: Sarvam's default reasoning model
    # (sarvam-105b) emits a verbose reasoning_content trace before the final
    # answer even with reasoning_effort left at its disabling default -- a
    # small budget gets consumed by that trace alone, leaving content: null.
    # Same failure mode already documented in extract_case_signals.py and
    # judge_real_outcomes.py; confirmed here empirically (150 tokens -> 36%
    # extraction-failure rate on a 157-case smoke test).
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=600)


def _infer_claim_amount(description: str) -> float:
    amounts = nlp.extract_amounts(description)
    return amounts[0] if amounts else DEFAULT_CLAIM_AMOUNT


def _stratified_sample(universe_by_cat: dict[str, list[str]], target: int) -> list[str]:
    total = sum(len(v) for v in universe_by_cat.values())
    rng = random.Random(RANDOM_SEED)
    sample: list[str] = []
    for cat, ids in universe_by_cat.items():
        # Every category gets at least min(30, its own size) so a small
        # category (e.g. consumer_complaints, employment) isn't sampled down
        # to statistical noise just because it's a small share of the whole.
        k = min(len(ids), max(min(30, len(ids)), round(target * len(ids) / total)))
        sample.extend(rng.sample(ids, k))
    return sample


def _metrics(y_true, y_pred, y_score) -> dict:
    p, r, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 3),
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(f1, 3),
        "roc_auc": round(roc_auc_score(y_true, y_score), 3) if len(set(y_true)) > 1 else None,
    }


def _make_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5))])


def _loo_accuracy(df: pd.DataFrame) -> dict:
    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = df["claimant_prevailed"].astype(int).to_numpy()
    loo = LeaveOneOut()
    y_pred = np.zeros(len(y), dtype=int)
    y_score = np.zeros(len(y), dtype=float)
    for train_idx, test_idx in loo.split(X):
        pipe = _make_pipeline()
        pipe.fit(X.iloc[train_idx], y[train_idx])
        y_pred[test_idx] = pipe.predict(X.iloc[test_idx])
        y_score[test_idx] = pipe.predict_proba(X.iloc[test_idx])[:, 1]
    return _metrics(y, y_pred, y_score)


def _permutation_test(df: pd.DataFrame, observed_acc: float, n_shuffles: int = 200) -> dict:
    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = df["claimant_prevailed"].astype(int).to_numpy()
    rng = np.random.RandomState(RANDOM_SEED)
    chance_accs = []
    # Full LOO per shuffle is too slow at 200 reps -- a single stratified
    # 5-fold CV per shuffle is the standard, much cheaper substitute for
    # estimating the null distribution's mean accuracy; only the ONE
    # observed-data score (above, from real LOO) is compared against it.
    from sklearn.model_selection import StratifiedKFold
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
    for _ in range(n_shuffles):
        y_shuffled = rng.permutation(y)
        accs = []
        for train_idx, test_idx in skf.split(X, y_shuffled):
            pipe = _make_pipeline()
            pipe.fit(X.iloc[train_idx], y_shuffled[train_idx])
            accs.append(accuracy_score(y_shuffled[test_idx], pipe.predict(X.iloc[test_idx])))
        chance_accs.append(np.mean(accs))
    chance_accs = np.array(chance_accs)
    p_value = float((chance_accs >= observed_acc).sum() + 1) / (n_shuffles + 1)
    return {"n_permutations": n_shuffles, "p_value": round(p_value, 4), "chance_accuracy_mean": round(float(chance_accs.mean()), 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quick simulation: raw-facts model vs. current heuristic, predicting REAL outcomes.")
    ap.add_argument("--sample", type=int, default=500, help="target sample size (stratified by category)")
    ap.add_argument("--fresh", action="store_true", help="ignore cached labels, re-extract everything")
    args = ap.parse_args(argv)

    if not llm.is_available():
        print("ERROR: LLM unavailable -- this simulation needs real extraction calls. Aborting.")
        return 1

    signals = {s["case_id"]: s for s in json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))}
    results = {r["case_id"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    universe = [v["case_id"] for v in verdicts if v["case_id"] in signals and v["case_id"] in results]

    by_cat: dict[str, list[str]] = {}
    for cid in universe:
        by_cat.setdefault(signals[cid]["category"], []).append(cid)
    sample_ids = _stratified_sample(by_cat, args.sample)
    print(f"Universe: {len(universe)} resolved cases across {len(by_cat)} categories. "
          f"Sampled {len(sample_ids)} (stratified).")

    cached: dict[str, dict] = {}
    if not args.fresh and LABELS_PATH.exists():
        cached = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        print(f"Loaded {len(cached)} cached label(s).")

    to_extract = [cid for cid in sample_ids if cid not in cached]
    print(f"{len(to_extract)} case(s) need label extraction.")
    consecutive_failures = 0
    for i, cid in enumerate(to_extract):
        case = signals[cid]
        text = case.get("expected_outcome") or case.get("case_description", "")
        label = _extract_real_outcome(text)
        if (label is None or "claimant_prevailed" not in label) and consecutive_failures >= 2:
            # Two failures in a row this run reproducibly hit a hard wall of
            # continuous failures starting ~75-100 calls in -- a rate limit
            # or circuit-breaker trip, not per-case content problems (the
            # SAME cids succeed on a fresh-process retry). Back off for the
            # breaker's own cooldown window before trying this case again
            # once, instead of burning through the rest of the batch as
            # instant, uninformative failures.
            time.sleep(32)
            label = _extract_real_outcome(text)
        if label is None or "claimant_prevailed" not in label:
            print(f"  [{i+1}/{len(to_extract)}] {cid}: EXTRACTION FAILED")
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        cached[cid] = label
        time.sleep(0.4)  # spread requests out -- avoid re-tripping the same rate limit
        if (i + 1) % 25 == 0 or i + 1 == len(to_extract):
            print(f"  [{i+1}/{len(to_extract)}] extracted ({sum(1 for v in cached.values() if v.get('claimant_prevailed'))} prevailed so far)")
            LABELS_PATH.write_text(json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8")
    LABELS_PATH.write_text(json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8")

    rows = []
    for cid in sample_ids:
        lbl = cached.get(cid)
        if lbl is None or lbl.get("claimant_prevailed") is None:
            continue
        sig = signals[cid]
        res = results[cid]
        sub = sig.get("signals") or {}
        has_support = sub.get("respondent_ground_has_specific_support")
        desc = sig["case_description"]
        rows.append({
            "case_id": cid,
            "category": sig["category"],
            "mapped_dispute_type": res.get("mapped_dispute_type"),
            "claim_amount_log": np.log1p(_infer_claim_amount(desc)),
            "claimant_evidence_count": sub.get("claimant_evidence_count") or 0,
            "description_word_count": len(desc.split()),
            "ingestion_confidence": res.get("ingestion_confidence"),
            "respondent_ground_has_specific_support": "unknown" if has_support is None else str(bool(has_support)),
            "respondent_accepts_liability": str(bool(sub.get("respondent_accepts_liability"))),
            "has_documentary_instrument": str(nlp.has_documentary_instrument(desc)),
            "claimant_strength_score": res.get("claimant_strength_score"),
            "respondent_strength_score": res.get("respondent_strength_score"),
            "claimant_prevailed": bool(lbl["claimant_prevailed"]),
            "split": _split_bucket(cid),
        })
    df = pd.DataFrame(rows)
    for col in ("ingestion_confidence",):
        df[col] = df[col].fillna(df[col].median())
    print(f"\nUsable rows: {len(df)} (dropped {len(sample_ids) - len(df)} with failed/missing labels)")

    reports: dict[str, dict] = {}
    for split_name in ("explore", "confirm"):
        sub_df = df[df["split"] == split_name].reset_index(drop=True)
        n = len(sub_df)
        n_prevail = int(sub_df["claimant_prevailed"].sum())
        majority_acc = max(n_prevail, n - n_prevail) / n if n else None
        has_c = sub_df["claimant_strength_score"].notna() & sub_df["respondent_strength_score"].notna()
        heuristic_pred = (sub_df.loc[has_c, "claimant_strength_score"] > sub_df.loc[has_c, "respondent_strength_score"]).astype(int)
        heuristic_actual = sub_df.loc[has_c, "claimant_prevailed"].astype(int)
        heuristic_acc = accuracy_score(heuristic_actual, heuristic_pred) if has_c.sum() else None

        print(f"\n=== {split_name.upper()} (n={n}, {n_prevail} prevailed = {n_prevail/n:.1%}) ===")
        print(f"  majority-class baseline accuracy: {majority_acc:.3f}" if majority_acc is not None else "  n/a")
        print(f"  CURRENT heuristic (net_strength>0) accuracy vs real outcome: {heuristic_acc:.3f}" if heuristic_acc is not None else "  n/a")

        model_metrics = _loo_accuracy(sub_df)
        print(f"  TRAINED MODEL (raw facts, LOO-CV): accuracy={model_metrics['accuracy']} "
              f"precision={model_metrics['precision']} recall={model_metrics['recall']} "
              f"f1={model_metrics['f1']} roc_auc={model_metrics['roc_auc']}")

        report_entry = {
            "split": split_name, "n": n, "n_prevail": n_prevail,
            "majority_baseline_accuracy": round(majority_acc, 3) if majority_acc is not None else None,
            "current_heuristic_accuracy": round(heuristic_acc, 3) if heuristic_acc is not None else None,
            "trained_model": model_metrics,
        }
        if split_name == "confirm":
            perm = _permutation_test(sub_df, model_metrics["accuracy"])
            print(f"  permutation test: p={perm['p_value']} (chance-level mean accuracy={perm['chance_accuracy_mean']}, {perm['n_permutations']} shuffles)")
            report_entry["permutation_test"] = perm
        reports[split_name] = report_entry

    OUT_PATH.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
