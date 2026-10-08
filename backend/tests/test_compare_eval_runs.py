"""Tests for the paired before/after comparison helpers: the exact McNemar test and the bootstrap interval."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import compare_eval_runs as cmp  # noqa: E402


class TestMcNemarExact(unittest.TestCase):
    def test_no_changed_cases_is_not_significant(self):
        self.assertEqual(cmp.mcnemar_exact(0, 0), 1.0)

    def test_balanced_changes_are_not_significant(self):
        self.assertEqual(cmp.mcnemar_exact(5, 5), 1.0)

    def test_all_changes_in_one_direction_matches_the_closed_form(self):
        # 10 improved, 0 worsened: two-sided p = 2 * 0.5**10
        self.assertAlmostEqual(cmp.mcnemar_exact(10, 0), 2 * 0.5 ** 10, places=9)
        self.assertAlmostEqual(cmp.mcnemar_exact(0, 10), cmp.mcnemar_exact(10, 0), places=12)  # symmetric

    def test_known_reference_value(self):
        # 8 improved vs 2 worsened: two-sided exact p = 0.109375 (scipy.stats.binomtest(2, 10, 0.5))
        self.assertAlmostEqual(cmp.mcnemar_exact(8, 2), 0.109375, places=6)

    def test_large_counts_are_stable_and_tiny_when_lopsided(self):
        p = cmp.mcnemar_exact(3000, 2400)
        self.assertTrue(0.0 <= p < 1e-6)
        self.assertEqual(cmp.mcnemar_exact(2700, 2700), 1.0)

    def test_p_value_never_exceeds_one(self):
        for up, down in [(1, 1), (3, 4), (50, 51)]:
            self.assertLessEqual(cmp.mcnemar_exact(up, down), 1.0)


class TestBootstrap(unittest.TestCase):
    def test_interval_is_deterministic_and_brackets_the_observed_difference(self):
        pairs = [(0, 1)] * 60 + [(1, 0)] * 20 + [(0, 0)] * 120
        lo, hi = cmp.bootstrap_diff(pairs)
        self.assertEqual((lo, hi), cmp.bootstrap_diff(pairs))
        observed = (60 - 20) / len(pairs)
        self.assertLess(lo, observed)
        self.assertGreater(hi, observed)
        self.assertGreater(lo, 0)  # a clear win: the interval excludes zero


if __name__ == "__main__":
    unittest.main()
