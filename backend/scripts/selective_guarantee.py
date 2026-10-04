"""Exact binomial bounds and fixed-sequence threshold calibration.

Two uses, one implementation:
  1. Certify how well the LLM judge agrees with humans (scripts/judge_human_agreement.py): "with 95%
     confidence the judge's agreement with a human is at least X".
  2. Calibrate an escalation threshold for the pipeline itself: find the loosest confidence cut-off
     such that, among the cases the AI keeps (does not escalate), the error rate is provably at most alpha.

Method (Jung, Brahman & Choi, "Trust or Escalate", ICLR 2025, eq. 3-5): treat each candidate threshold as a
hypothesis "risk <= alpha", test them from the strictest down with an exact one-sided binomial upper
confidence bound (fixed-sequence testing, so no multiple-testing correction is needed), and stop at the first
threshold that fails. If the calibration examples are exchangeable with future ones, the selected threshold
keeps the risk <= alpha with probability >= 1 - delta.

Pure Python (no scipy) so it runs wherever the backend runs.
"""
from __future__ import annotations

import math
from bisect import bisect_left


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(Bin(n, p) <= k), summed in log space so it stays stable for n in the thousands."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    lg_n = math.lgamma(n + 1)
    total = 0.0
    for i in range(k + 1):
        total += math.exp(lg_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(total, 1.0)


def exact_upper_bound(errors: int, n: int, delta: float = 0.05) -> float:
    """One-sided (1 - delta) exact upper confidence bound on an error rate:
    sup{R : P(Bin(n, R) <= errors) >= delta}. Returns 1.0 when n == 0 (nothing is known)."""
    if n <= 0:
        return 1.0
    if errors >= n:
        return 1.0
    lo, hi = errors / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binom_cdf(errors, n, mid) >= delta:
            lo = mid
        else:
            hi = mid
    return lo


def agreement_lower_bound(agree: int, n: int, delta: float = 0.05) -> float:
    """One-sided (1 - delta) exact lower bound on an agreement rate, given `agree` of `n` agreed."""
    return 1.0 - exact_upper_bound(n - agree, n, delta)


def labels_needed(agree_rate: float, target: float, delta: float = 0.05, start: int = 10, cap: int = 5000) -> int | None:
    """Smallest n at which the exact lower bound reaches `target` if the observed agreement rate stayed
    `agree_rate`. None when the observed rate is itself below the target (no n will certify it) or the cap
    is hit. Planning aid only: the real rate will move as labels come in."""
    if agree_rate <= target:
        return None
    n = max(start, 1)
    while n <= cap:
        errors = round((1.0 - agree_rate) * n)
        if agreement_lower_bound(n - errors, n, delta) >= target:
            return n
        n += 1 if n < 200 else 10
    return None


DEFAULT_GRID = tuple(round(0.999 - 0.005 * i, 3) for i in range(100))  # 0.999 down to ~0.504


def fixed_sequence_threshold(confidences, correct, alpha: float, delta: float = 0.05, grid=DEFAULT_GRID) -> dict:
    """Pick the loosest confidence threshold whose KEPT set (confidence >= threshold) has error rate <= alpha
    with probability >= 1 - delta, by fixed-sequence testing down `grid` (must be fixed in advance and sorted
    from strictest to loosest; it must not be chosen by looking at `correct`).

    Returns {"threshold", "coverage", "kept", "errors", "empirical_risk", "risk_upper_bound"}.
    threshold is None when even the strictest grid point cannot be certified (the guarantee cannot be met at
    this alpha with this much data, so everything should be escalated)."""
    pairs = sorted(zip(confidences, correct), key=lambda t: t[0])
    confs = [c for c, _ in pairs]
    total = len(pairs)
    # suffix_err[i] = number of errors among pairs[i:], so kept-set errors at a threshold are O(1) after one bisect
    suffix_err = [0] * (total + 1)
    for i in range(total - 1, -1, -1):
        suffix_err[i] = suffix_err[i + 1] + (0 if pairs[i][1] else 1)

    best = None
    for lam in grid:
        idx = bisect_left(confs, lam)
        kept = total - idx
        if kept == 0:
            continue  # nothing is kept at this cut-off: no evidence either way, so it neither passes nor ends the
            # sequence (this depends only on the confidences, never the labels, so the guarantee is unaffected)
        errors = suffix_err[idx]
        bound = exact_upper_bound(errors, kept, delta)
        if bound > alpha:
            break
        best = {"threshold": lam, "coverage": kept / total if total else 0.0, "kept": kept, "errors": errors,
                "empirical_risk": errors / kept if kept else 0.0, "risk_upper_bound": bound}
    if best is None:
        return {"threshold": None, "coverage": 0.0, "kept": 0, "errors": 0, "empirical_risk": None, "risk_upper_bound": None}
    return best
