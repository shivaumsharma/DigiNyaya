"""Tests for the eval-harness amount helpers: the judge's numeric tolerance and
the claimed-amount fallback used when a case description states no figure."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_real_outcomes import _amount_within_tolerance  # noqa: E402
from scripts.run_real_judgment_eval import DEFAULT_CLAIM_AMOUNT, _infer_claim_amount  # noqa: E402


def _ai(amount, relief_type="full_refund"):
    return {"relief_type": relief_type, "relief_amount": amount}


class TestAmountTolerance(unittest.TestCase):
    real = {"expected_outcome": "The court decreed Rs. 2,00,000/- with interest, total decree Rs. 2,30,000/-."}

    def test_within_twenty_percent_of_principal(self):
        self.assertTrue(_amount_within_tolerance(self.real, _ai(190000.0)))

    def test_matches_the_total_decree_figure_too(self):
        self.assertTrue(_amount_within_tolerance(self.real, _ai(235000.0)))

    def test_far_off_amount_fails(self):
        self.assertFalse(_amount_within_tolerance(self.real, _ai(20000.0)))

    def test_not_applicable_for_non_monetary_relief(self):
        self.assertIsNone(_amount_within_tolerance(self.real, _ai(5000.0, "injunction")))

    def test_not_applicable_when_real_outcome_has_no_figure(self):
        self.assertIsNone(_amount_within_tolerance({"expected_outcome": "Suit decreed."}, _ai(1000.0)))

    def test_not_applicable_for_zero_ai_amount(self):
        self.assertIsNone(_amount_within_tolerance(self.real, _ai(0.0)))


class TestClaimAmountFallback(unittest.TestCase):
    def test_description_figure_wins(self):
        c = {"case_description": "Suit for Rs. 74,01,250/-.", "claimed_amount_rupees": 5.0}
        self.assertEqual(_infer_claim_amount(c), 7401250.0)

    def test_extracted_amount_used_when_description_has_none(self):
        c = {"case_description": "Recovery of a specified principal sum.", "claimed_amount_rupees": 400000}
        self.assertEqual(_infer_claim_amount(c), 400000.0)

    def test_default_only_when_nothing_is_known(self):
        c = {"case_description": "Recovery of a specified principal sum.", "claimed_amount_rupees": None}
        self.assertEqual(_infer_claim_amount(c), DEFAULT_CLAIM_AMOUNT)

    def test_legacy_string_argument_still_works(self):
        self.assertEqual(_infer_claim_amount("Claim of Rs. 10,000."), 10000.0)


if __name__ == "__main__":
    unittest.main()
