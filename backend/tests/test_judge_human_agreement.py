"""Tests for the judge-vs-human agreement script: kappa, bootstrap intervals, handling of
unsure/incomplete rows, and the multi-labeller end-to-end path."""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import judge_human_agreement as jha  # noqa: E402

FIELDS = ["case_id", "facts", "REAL_COURT_DECIDED", "AI_DECIDED_order",
          "claimant_prevailed_ai", "claimant_prevailed_real", "relief_similar"]


def _write_sheet(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for cid, ai, real, sim in rows:
            w.writerow({"case_id": cid, "facts": "f", "REAL_COURT_DECIDED": "r", "AI_DECIDED_order": "o",
                        "claimant_prevailed_ai": ai, "claimant_prevailed_real": real, "relief_similar": sim})


class TestKappa(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertAlmostEqual(jha.kappa([(True, True), (False, False), (True, True), (False, False)]), 1.0)

    def test_chance_level_agreement_is_zero(self):
        pairs = [(True, True), (True, False), (False, True), (False, False)] * 5
        self.assertAlmostEqual(jha.kappa(pairs), 0.0)

    def test_known_value_two_by_two(self):
        # 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po=.7, pe=.25*.. = .5 -> kappa=.4
        pairs = [(True, True)] * 20 + [(True, False)] * 5 + [(False, True)] * 10 + [(False, False)] * 15
        self.assertAlmostEqual(jha.kappa(pairs), 0.4)

    def test_multiclass_labels(self):
        pairs = [("match", "match"), ("partial", "partial"), ("mismatch", "mismatch"), ("match", "partial")]
        # po = .75; pe = .5*.25 + .25*.5 + .25*.25 = .3125
        self.assertAlmostEqual(jha.kappa(pairs), (0.75 - 0.3125) / (1 - 0.3125), places=6)

    def test_degenerate_cases_are_none(self):
        self.assertIsNone(jha.kappa([]))
        self.assertIsNone(jha.kappa([(True, True)] * 4))  # no variation: kappa undefined


class TestBootstrap(unittest.TestCase):
    def test_ci_is_deterministic_and_contains_estimate(self):
        pairs = [(True, True)] * 40 + [(True, False)] * 10
        a, b = jha.bootstrap_ci(pairs, jha.agreement), jha.bootstrap_ci(pairs, jha.agreement)
        self.assertEqual(a, b)
        self.assertLessEqual(a[0], jha.agreement(pairs))
        self.assertGreaterEqual(a[1], jha.agreement(pairs))

    def test_tiny_sample_returns_none(self):
        self.assertIsNone(jha.bootstrap_ci([(True, True)], jha.agreement))


class TestVerdictAndReading(unittest.TestCase):
    def test_verdict_of(self):
        self.assertEqual(jha.verdict_of(True, False, None), "mismatch")
        self.assertEqual(jha.verdict_of(True, True, True), "match")
        self.assertIsNone(jha.verdict_of(False, False, None))  # both lost: not comparable with the judge's call
        self.assertIsNone(jha.verdict_of(True, True, None))  # similarity unanswered: never silently "partial"

    def test_both_won_with_blank_similarity_has_no_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.csv"
            _write_sheet(p, [("c1", "Y", "Y", ""), ("c2", "Y", "Y", "Y"), ("c3", "?", "N", ""), ("c4", "Y", "N", "")])
            got = jha.read_labels(str(p))
        self.assertIsNone(got["c1"]["verdict"])
        self.assertEqual(got["c2"]["verdict"], "match")
        self.assertIsNone(got["c3"]["verdict"])  # unsure
        self.assertEqual(got["c4"]["verdict"], "mismatch")
        self.assertEqual((got["c3"]["ai"], got["c3"]["real"]), (None, False))


class TestEndToEnd(unittest.TestCase):
    def test_two_labellers_and_disagreements_file(self):
        key = {
            "c1": {"claimant_prevailed_ai": True, "claimant_prevailed_real": True, "relief_similar": True, "verdict": "match"},
            "c2": {"claimant_prevailed_ai": True, "claimant_prevailed_real": False, "relief_similar": False, "verdict": "mismatch"},
            "c3": {"claimant_prevailed_ai": True, "claimant_prevailed_real": True, "relief_similar": False, "verdict": "partial"},
        }
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "key.json").write_text(json.dumps(key), encoding="utf-8")
            _write_sheet(d / "a.csv", [("c1", "Y", "Y", "Y"), ("c2", "Y", "N", ""), ("c3", "Y", "Y", "Y")])  # c3 differs from judge
            _write_sheet(d / "b.csv", [("c1", "Y", "Y", "Y"), ("c2", "Y", "N", ""), ("c3", "Y", "Y", "N")])
            out = d / "dis.csv"
            argv = ["--labels", str(d / "a.csv"), str(d / "b.csv"), "--names", "a", "b",
                    "--key", str(d / "key.json"), "--disagreements", str(out)]
            old = sys.argv
            sys.argv = ["judge_human_agreement"] + argv
            try:
                self.assertEqual(jha.main(), 0)
            finally:
                sys.argv = old
            with open(out, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["case_id"] for r in rows], ["c3"])
        self.assertEqual(rows[0]["judge_verdict"], "partial")
        self.assertEqual(rows[0]["a_verdict"], "match")
        self.assertEqual(rows[0]["b_verdict"], "partial")


if __name__ == "__main__":
    unittest.main()
