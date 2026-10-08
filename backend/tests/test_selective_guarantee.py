"""Tests for the exact binomial bounds and the fixed-sequence threshold calibration."""
from __future__ import annotations

import os
import random
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import selective_guarantee as sg  # noqa: E402


class TestExactBounds(unittest.TestCase):
    # Reference values are one-sided Clopper-Pearson upper bounds (scipy.stats.beta.ppf(1 - delta, k + 1, n - k)).
    def test_matches_clopper_pearson_reference_values(self):
        for k, n, d, ref in [(1, 10, 0.05, 0.394163), (5, 50, 0.1, 0.177618),
                             (30, 150, 0.05, 0.261347), (12, 400, 0.1, 0.044132)]:
            self.assertAlmostEqual(sg.exact_upper_bound(k, n, d), ref, places=5, msg=f"{k}/{n} d={d}")

    def test_zero_errors_has_closed_form(self):
        for n in (10, 100, 1000):
            self.assertAlmostEqual(sg.exact_upper_bound(0, n, 0.05), 1 - 0.05 ** (1 / n), places=6)

    def test_edge_cases(self):
        self.assertEqual(sg.exact_upper_bound(0, 0), 1.0)  # no data: nothing known
        self.assertEqual(sg.exact_upper_bound(10, 10), 1.0)
        self.assertEqual(sg.binom_cdf(-1, 10, 0.3), 0.0)
        self.assertEqual(sg.binom_cdf(10, 10, 0.3), 1.0)

    def test_more_errors_means_looser_bound_and_more_data_tighter(self):
        self.assertLess(sg.exact_upper_bound(2, 100), sg.exact_upper_bound(5, 100))
        self.assertLess(sg.exact_upper_bound(20, 1000), sg.exact_upper_bound(2, 100))

    def test_agreement_lower_bound_is_the_mirror(self):
        self.assertAlmostEqual(sg.agreement_lower_bound(9, 10, 0.05), 1 - 0.394163, places=5)

    def test_binom_cdf_is_stable_for_large_n(self):
        self.assertAlmostEqual(sg.binom_cdf(500, 1000, 0.5), 0.5126, places=3)


class TestLabelsNeeded(unittest.TestCase):
    def test_none_when_observed_rate_is_below_target(self):
        self.assertIsNone(sg.labels_needed(0.75, 0.80))

    def test_found_n_actually_certifies_and_one_less_does_not_need_to(self):
        n = sg.labels_needed(0.90, 0.80, delta=0.05)
        self.assertIsNotNone(n)
        errors = round(0.10 * n)
        self.assertGreaterEqual(sg.agreement_lower_bound(n - errors, n, 0.05), 0.80)

    def test_higher_target_needs_more_labels(self):
        self.assertGreater(sg.labels_needed(0.95, 0.90), sg.labels_needed(0.95, 0.80))


class TestFixedSequenceThreshold(unittest.TestCase):
    def test_returns_none_when_nothing_can_be_certified(self):
        r = sg.fixed_sequence_threshold([0.9, 0.8, 0.7], [False, False, True], alpha=0.05)
        self.assertIsNone(r["threshold"])
        self.assertEqual(r["coverage"], 0.0)

    def test_perfectly_calibrated_high_confidence_cases_are_kept(self):
        confs = [0.95] * 400 + [0.55] * 400
        correct = [True] * 400 + [i % 2 == 0 for i in range(400)]
        r = sg.fixed_sequence_threshold(confs, correct, alpha=0.1)
        self.assertIsNotNone(r["threshold"])
        self.assertAlmostEqual(r["coverage"], 0.5)  # only the 0.95 group is kept
        self.assertEqual(r["errors"], 0)

    def test_guarantee_holds_empirically_on_calibrated_data(self):
        """Monte Carlo: with confidence ~ U(0.5, 1) and correct ~ Bernoulli(confidence), the true error among
        kept cases at threshold t is (1 - t) / 2. The selected threshold must keep that <= alpha in >= 1 - delta
        of repeated calibrations (allowing sampling slack on the trial count)."""
        rng = random.Random(11)
        alpha, delta, trials, violations = 0.15, 0.1, 80, 0
        for _ in range(trials):
            confs = [rng.uniform(0.5, 1.0) for _ in range(500)]
            correct = [rng.random() < c for c in confs]
            r = sg.fixed_sequence_threshold(confs, correct, alpha=alpha, delta=delta)
            if r["threshold"] is not None and (1 - r["threshold"]) / 2 > alpha:
                violations += 1
        self.assertLessEqual(violations / trials, delta + 0.08)

    def test_stricter_alpha_never_increases_coverage(self):
        rng = random.Random(3)
        confs = [rng.uniform(0.5, 1.0) for _ in range(600)]
        correct = [rng.random() < c for c in confs]
        loose = sg.fixed_sequence_threshold(confs, correct, alpha=0.25)
        strict = sg.fixed_sequence_threshold(confs, correct, alpha=0.10)
        self.assertGreaterEqual(loose["coverage"], strict["coverage"])


if __name__ == "__main__":
    unittest.main()
