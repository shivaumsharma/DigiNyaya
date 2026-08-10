"""Diagnostic-gated calibration for app.core.safety_gate's confidence-based
escalation decision.

This is deliberately NOT a "run it and get a threshold" script. Conformal /
risk-controlling calibration (this is closer to Learn-Then-Test or an exact
Clopper-Pearson bound on a Bernoulli risk than classic split-conformal
prediction SETS -- there's no set being predicted here, just a binary
escalate/don't-escalate decision) only produces a trustworthy threshold if
the signal being thresholded actually correlates with real-world
correctness. It does not manufacture that correlation -- if the signal is
noise, calibration either degenerates to "escalate almost everything" or
produces a confident-looking number that doesn't generalize.

So: this script's FIRST job, every run, is to check whether any candidate
signal (composite_confidence, ingestion_confidence, precedent count, ...)
actually discriminates correct-vs-incorrect outcomes, via AUC and
point-biserial correlation against real ground truth. Only a signal that
clears MIN_USABLE_AUC gets a threshold computed for it at all. Anything
weaker is reported as exactly that -- "not usable yet" -- never silently
skipped or dressed up.

Two ground-truth sources, growing over time, merged into one calibration set:
  1. backend/data_cache/real_judgment_verdict_comparison.json -- the
     scripts/judge_real_outcomes.py output: does the AI's actual decision
     match what a real court decided, for cases run through the real
     5-agent pipeline against real Indian Kanoon judgments. Fixed size
     (growing this costs real Indian Kanoon API calls).
  2. Real reviewer decisions (db.all_cases(), any case with a
     reviewer_decision set) -- approved=True is treated as "the AI's
     resolution was correct", approved=False as "incorrect". This is the
     source the original proposal focused on; it's ~empty today (the
     reviewer workflow just shipped) but grows for free as the app is used,
     with no query cost.

Never writes a threshold into app/core/safety_gate.py -- this is read-only
analysis. Usage (from the backend folder):
    python -m scripts.calibrate_safety_gate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app import db  # noqa: E402

DATA_CACHE = Path(__file__).resolve().parents[1] / "data_cache"
VERDICTS_PATH = DATA_CACHE / "real_judgment_verdict_comparison.json"
EVAL_RESULTS_PATH = DATA_CACHE / "real_judgment_eval_results.json"

# A signal must clear this AUC before a threshold is even computed for it.
# 0.5 = coin flip; this is a deliberately modest "at least clearly better
# than nothing" bar, not "AUC 0.7+ from a rigorous ML deployment" -- stated
# explicitly here so it's a documented, revisitable choice, not a silent one.
MIN_USABLE_AUC = 0.65

# Candidate signals to test -- add new ones here as the pipeline grows new
# scalar confidence-like outputs. Each is a (label, extractor) pair; the
# extractor takes a joined case-feature dict and returns a float or None.
CANDIDATE_SIGNALS = {
    "composite_confidence": lambda c: c.get("composite_confidence"),
    "ingestion_confidence": lambda c: c.get("ingestion_confidence"),
    "precedents_retrieved": lambda c: c.get("precedents_retrieved"),
}


def _auc(pairs: list[tuple[float, int]]) -> tuple[float, int, int] | None:
    """Mann-Whitney-style AUC: P(a random positive scores higher than a
    random negative), with ties counting as 0.5. Returns (auc, n_pos, n_neg)
    or None if either class is empty."""
    positives = [x for x, y in pairs if y == 1]
    negatives = [x for x, y in pairs if y == 0]
    if not positives or not negatives:
        return None
    concordant = sum(1 for p in positives for n in negatives if p > n)
    tied = sum(1 for p in positives for n in negatives if p == n)
    total = len(positives) * len(negatives)
    return (concordant + 0.5 * tied) / total, len(positives), len(negatives)


def _point_biserial(pairs: list[tuple[float, int]]) -> float | None:
    n = len(pairs)
    if n < 2:
        return None
    xs = [x for x, _ in pairs]
    ys = [float(y) for _, y in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def _clopper_pearson_upper(failures: int, n: int, confidence: float) -> float:
    """Exact one-sided Clopper-Pearson upper bound on a Bernoulli failure
    rate -- appropriate here (not an asymptotic/normal approximation) since
    calibration sets this small (tens of cases) are exactly where asymptotic
    bounds become unreliable. Uses the Beta-distribution identity for the
    Clopper-Pearson interval, no scipy dependency needed."""
    if failures == n:
        return 1.0
    if failures == 0:
        # Upper bound solves (1-p)^n = alpha for p.
        return 1 - (1 - confidence) ** (1 / n)

    # Beta(failures+1, n-failures) quantile at `confidence`, via bisection
    # (avoids a scipy/statistics dependency this codebase doesn't otherwise need).
    from math import comb

    def beta_cdf(p: float, a: int, b: int) -> float:
        # Regularized incomplete beta via direct binomial-tail summation
        # (a, b small integers here -- tens, not thousands -- so this is exact
        # and fast; not the general-purpose approach for large parameters).
        return sum(comb(a + b - 1, k) * p**k * (1 - p) ** (a + b - 1 - k) for k in range(a, a + b))

    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        # P(X <= failures-1 | n trials, true rate mid) -- solving for the p
        # at which the observed failure count sits at the `confidence` tail.
        cdf = beta_cdf(mid, failures, n - failures + 1)
        if cdf > 1 - confidence:
            hi = mid
        else:
            lo = mid
    return hi


def _load_eval_calibration_rows() -> list[dict]:
    if not VERDICTS_PATH.exists() or not EVAL_RESULTS_PATH.exists():
        return []
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    eval_results = {e["case_id"]: e for e in json.loads(EVAL_RESULTS_PATH.read_text(encoding="utf-8"))}
    rows = []
    for v in verdicts:
        e = eval_results.get(v["case_id"])
        if e is None:
            continue
        # "partial" counted as a miss -- conservative (a guarantee on "fully
        # matches a real court" shouldn't quietly count a half-right answer
        # as a success).
        correct = 1 if v["verdict"] == "match" else 0
        row = dict(e)
        row["correct"] = correct
        row["source"] = "real_judgment_eval"
        rows.append(row)
    return rows


def _load_reviewer_calibration_rows() -> list[dict]:
    """Real reviewer decisions -- the data source the escalation workflow
    itself generates for free as the app is used. Empty today; this is
    infrastructure for it to matter once it isn't."""
    rows = []
    for case in db.all_cases():
        decision = case.get("reviewer_decision")
        if not decision:
            continue
        resolution = case.get("resolution") or {}
        composite = resolution.get("composite_confidence")
        composite_score = composite.get("score") if isinstance(composite, dict) else None
        rows.append({
            "case_id": case["case_id"],
            "composite_confidence": composite_score,
            "ingestion_confidence": None,  # not persisted on the case dict today
            "precedents_retrieved": None,
            "correct": 1 if decision.get("approved") else 0,
            "source": "reviewer_decision",
        })
    return rows


def main() -> None:
    eval_rows = _load_eval_calibration_rows()
    reviewer_rows = _load_reviewer_calibration_rows()
    rows = eval_rows + reviewer_rows

    print("=" * 72)
    print("SAFETY GATE CALIBRATION -- DIAGNOSTIC REPORT")
    print("=" * 72)
    print(f"Calibration rows: {len(rows)}  "
          f"({len(eval_rows)} from real-judgment eval, {len(reviewer_rows)} from real reviewer decisions)")
    if not rows:
        print("No calibration data at all -- nothing to report.")
        return

    print()
    print(f"{'signal':<24}{'n':>5}{'AUC':>8}{'r':>8}   verdict")
    print("-" * 72)

    usable_signals: dict[str, tuple[float, list[tuple[float, int]]]] = {}
    for label, extractor in CANDIDATE_SIGNALS.items():
        pairs = [(extractor(r), r["correct"]) for r in rows]
        pairs = [(float(x), y) for x, y in pairs if x is not None]
        if len(pairs) < 4:
            print(f"{label:<24}{len(pairs):>5}{'--':>8}{'--':>8}   insufficient data (need >= 4, have {len(pairs)})")
            continue
        auc_result = _auc(pairs)
        r = _point_biserial(pairs)
        if auc_result is None:
            print(f"{label:<24}{len(pairs):>5}{'--':>8}{'--':>8}   only one outcome class present, AUC undefined")
            continue
        auc, n_pos, n_neg = auc_result
        r_str = f"{r:.3f}" if r is not None else "--"
        if auc >= MIN_USABLE_AUC:
            verdict = f"USABLE (>= {MIN_USABLE_AUC} bar) -- {n_pos} correct / {n_neg} incorrect"
            usable_signals[label] = (auc, pairs)
        else:
            verdict = f"NOT usable (< {MIN_USABLE_AUC} bar) -- {n_pos} correct / {n_neg} incorrect"
        print(f"{label:<24}{len(pairs):>5}{auc:>8.3f}{r_str:>8}   {verdict}")

    print()
    if not usable_signals:
        print(
            "No candidate signal clears the usability bar. Do NOT calibrate an\n"
            "escalation threshold on any of these right now -- doing so would produce\n"
            "a confident-looking number with no real discriminating power behind it.\n"
            "This needs either more calibration data (both sources above grow over\n"
            "time) or a better-constructed signal (e.g. a combination of features,\n"
            "or a purpose-built correctness classifier) before this is meaningful."
        )
        return

    print("Threshold search for usable signal(s) -- exact Clopper-Pearson upper")
    print("bound on the failure rate among cases with signal >= tau:")
    print()
    for label, (auc, pairs) in usable_signals.items():
        print(f"--- {label} (AUC={auc:.3f}) ---")
        candidate_taus = sorted({round(x, 3) for x, _ in pairs})
        for target_risk, confidence in [(0.10, 0.95), (0.05, 0.95)]:
            best_tau = None
            best_n = 0
            for tau in candidate_taus:
                subset = [(x, y) for x, y in pairs if x >= tau]
                n = len(subset)
                if n < 5:  # too few cases at this cut to bound anything meaningfully
                    continue
                failures = sum(1 for _, y in subset if y == 0)
                upper = _clopper_pearson_upper(failures, n, confidence)
                if upper <= target_risk:
                    best_tau, best_n = tau, n
                    break  # candidate_taus ascending -> first hit is the most permissive
            if best_tau is None:
                print(
                    f"  target risk <= {target_risk:.0%} at {confidence:.0%} confidence: "
                    f"NOT ACHIEVABLE with current data (n={len(pairs)}) -- would need more calibration cases."
                )
            else:
                print(
                    f"  target risk <= {target_risk:.0%} at {confidence:.0%} confidence: "
                    f"tau = {best_tau} (n={best_n} cases at or above this cut in the calibration set)"
                )
        print()


if __name__ == "__main__":
    main()
