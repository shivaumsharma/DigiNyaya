"""Tests for the outcome-leakage audit: phrase detection (and what must NOT be flagged), judge-name detection,
amount normalisation, the description-only predictability check, and the CLI end to end."""
from __future__ import annotations

import csv
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import audit_outcome_leakage as al  # noqa: E402


def _case(cid, desc, outcome="", cat="small_claims_debt_recovery"):
    return {"case_id": cid, "category": cat, "case_description": desc, "expected_outcome": outcome}


class TestPhrases(unittest.TestCase):
    def test_flags_result_revealing_language(self):
        for text in ("The court decreed the suit in favour of the plaintiff.",
                     "The suit was dismissed for want of proof.",
                     "The appeal is partly allowed.",
                     "The tenant is liable to pay arrears.",
                     "The court ordered the seller to refund the price.",
                     "Judgment was pronounced on 4 May."):
            self.assertTrue(al.audit_text(_case("x", text))["outcome_language"], text)

    def test_does_not_flag_a_normal_claim_narrative(self):
        for text in ("The claimant paid Rs. 5,000 and seeks a decree for refund of that sum.",
                     "The respondent denies liability and says the goods were delivered.",
                     "The plaintiff filed a suit for recovery and relies on a promissory note.",
                     "The tenant argues the notice was invalid."):
            self.assertEqual(al.audit_text(_case("x", text))["outcome_language"], [], text)

    def test_detects_named_judges_including_the_real_leak_example(self):
        for text in ("The case was presented before the Principal Junior Civil Judge, presided over by Smt. K. Pooja.",
                     "Heard before Hon'ble Justice Rao.",
                     "The matter came before Justice Sharma."):
            self.assertTrue(al.audit_text(_case("x", text))["names_judge"], text)

    def test_does_not_flag_court_named_without_a_judge(self):
        for text in ("The suit was filed in the Court of Civil Judge at Malkajgiri.",
                     "Before the Judicial Magistrate First Class at Pune the claim was heard.",
                     "The claimant says there was no access to Justice Department records.",
                     "The Civil Judge, Senior Division heard the suit."):
            self.assertFalse(al.audit_text(_case("x", text))["names_judge"], text)

    def test_amounts_normalise_indian_grouping_and_ignore_small_figures(self):
        self.assertEqual(al.amounts("Rs. 2,00,000/- and ₹50,000 and Rs. 500 and INR 1,25,000.50"), {200000, 50000, 125000})
        self.assertEqual(al.amounts(None), set())

    def test_amount_echo(self):
        r = al.audit_text(_case("x", "The seller kept Rs. 80,000.", outcome="Refund of Rs. 80,000 ordered."))
        self.assertEqual(r["amount_echo"], [80000])
        self.assertEqual(al.audit_text(_case("x", "Seller kept Rs. 80,000.", outcome="Refund of Rs. 60,000"))["amount_echo"], [])


class TestDescriptionOnlyPredictability(unittest.TestCase):
    def _data(self, leak: bool, n: int = 160):
        rng = random.Random(5)
        cases, verdicts = [], {}
        filler = ["seller", "invoice", "delivery", "payment", "notice", "refund", "contract", "goods", "defect", "delay"]
        for i in range(n):
            won = i % 2 == 0
            words = [rng.choice(filler) for _ in range(25)]
            if leak:
                words += ["decreed", "granted"] * 3 if won else ["dismissed", "rejected"] * 3
            cases.append(_case(f"c{i}", " ".join(words)))
            verdicts[f"c{i}"] = {"case_id": f"c{i}", "claimant_prevailed_real": won}
        return cases, verdicts

    def test_leaking_text_is_predictable_and_clean_text_is_not(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("scikit-learn not installed")
        leaky = al.description_only_cv(*self._data(True))
        clean = al.description_only_cv(*self._data(False))
        self.assertGreater(leaky["lift_over_majority"], 0.3)
        self.assertLess(clean["lift_over_majority"], 0.2)

    def test_returns_none_with_too_little_data_or_one_class(self):
        cases, verdicts = self._data(True, n=20)
        self.assertIsNone(al.description_only_cv(cases, verdicts))
        for v in verdicts.values():
            v["claimant_prevailed_real"] = True
        cases, _ = self._data(True)
        self.assertIsNone(al.description_only_cv(cases, {c["case_id"]: {"claimant_prevailed_real": True} for c in cases}))


class TestPairedComparisonWithThePipeline(unittest.TestCase):
    def _data(self, pipeline_accuracy: float, n: int = 200):
        rng = random.Random(11)
        cases, verdicts = [], {}
        for i in range(n):
            won = i % 2 == 0
            words = ["seller", "invoice", "payment", "notice"] * 5 + (["decreed", "granted"] * 3 if won else ["dismissed", "rejected"] * 3)
            cases.append(_case(f"c{i}", " ".join(words)))
            ai = won if rng.random() < pipeline_accuracy else (not won)
            verdicts[f"c{i}"] = {"case_id": f"c{i}", "claimant_prevailed_real": won, "claimant_prevailed_ai": ai}
        return cases, verdicts

    def test_reports_macro_f1_recalls_and_a_paired_test(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("scikit-learn not installed")
        cv = al.description_only_cv(*self._data(0.6))
        self.assertIn("macro_f1", cv)
        self.assertTrue(0.0 <= cv["recall_claimant_wins"] <= 1.0 and 0.0 <= cv["recall_respondent_wins"] <= 1.0)
        pl = cv["pipeline"]
        self.assertEqual(pl["n"], 200)
        # the leaky text classifier is near-perfect and the pipeline is ~60% accurate: text should win clearly
        self.assertGreater(pl["text_right_pipeline_wrong"], pl["pipeline_right_text_wrong"])
        self.assertLess(pl["mcnemar_p"], 0.05)
        self.assertAlmostEqual(pl["accuracy"], 0.6, delta=0.12)

    def test_no_pipeline_block_when_verdicts_lack_the_pipelines_call(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("scikit-learn not installed")
        cases, verdicts = self._data(0.6)
        for v in verdicts.values():
            v.pop("claimant_prevailed_ai")
        self.assertNotIn("pipeline", al.description_only_cv(cases, verdicts))

    def test_macro_f1_helper(self):
        f1, rec1, rec0 = al._macro_f1([1, 1, 0, 0], [1, 1, 0, 0])
        self.assertEqual((f1, rec1, rec0), (1.0, 1.0, 1.0))
        f1, rec1, rec0 = al._macro_f1([1, 1, 1, 0], [1, 1, 1, 1])  # always predicts class 1
        self.assertEqual((rec1, rec0), (1.0, 0.0))
        self.assertLess(f1, 0.5)

    def test_matched_text_records_the_triggering_phrase(self):
        r = al.audit_text(_case("x", "The court decreed the suit in favour of the plaintiff."))
        self.assertIn("court held/ordered", r["matched_text"])
        self.assertTrue(r["matched_text"]["court held/ordered"].lower().startswith("the court decreed"))


class TestCli(unittest.TestCase):
    def test_runs_end_to_end_and_writes_flagged_csv(self):
        cases = [
            _case("a", "The tenant stopped paying rent. The court decreed eviction.", cat="tenancy_disputes"),
            _case("b", "A seller delivered late goods; the buyer seeks a refund of Rs. 9,000.", "Refund of Rs. 9,000.", "consumer_complaints"),
            _case("c", "Heard before Hon'ble Justice Rao on a loan recovery claim."),
        ]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "ds.json").write_text(json.dumps(cases), encoding="utf-8")
            out = d / "flagged.csv"
            old_argv, old_stdout = sys.argv, sys.stdout
            sys.argv = ["x", "--dataset", str(d / "ds.json"), "--verdicts", str(d / "none.json"), "--flagged", str(out)]
            with open(d / "log.txt", "w", encoding="utf-8") as f:
                sys.stdout = f
                try:
                    self.assertEqual(al.main(), 0)
                finally:
                    sys.stdout, sys.argv = old_stdout, old_argv
            log = (d / "log.txt").read_text(encoding="utf-8")
            with open(out, encoding="utf-8-sig", newline="") as f:
                flagged = {r["case_id"]: r for r in csv.DictReader(f)}
        self.assertEqual(set(flagged), {"a", "c"})
        self.assertIn("OUTCOME LANGUAGE", log)
        self.assertIn("AMOUNT ECHO", log)
        self.assertIn("skipped", log)  # no verdicts file: predictability check is skipped, not crashed

    def test_missing_dataset_returns_error_code(self):
        old_argv, old_stdout = sys.argv, sys.stdout
        sys.argv = ["x", "--dataset", "/nonexistent/ds.json"]
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        try:
            self.assertEqual(al.main(), 1)
        finally:
            sys.stdout.close()
            sys.stdout, sys.argv = old_stdout, old_argv


if __name__ == "__main__":
    unittest.main()
