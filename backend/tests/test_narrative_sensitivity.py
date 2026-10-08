"""Tests for the narrative-sensitivity experiment: the perturbations only touch presentation (never the facts),
the per-case comparison and monotonicity logic are correct, and the CLI runs end to end on its demo cases."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.context import CaseContext  # noqa: E402
from scripts import narrative_sensitivity as ns  # noqa: E402


def _ctx(**kw):
    base = dict(case_id="DN-NS-1", owner_id="u", dispute_type="consumer_dispute", claimant_name="A", respondent_name="B",
                claim_amount=5000.0, description="First sentence here. Second sentence follows. Third.",
                evidence=[{"filename": "i.pdf", "kind": "invoice"}],
                respondent_submission={"statement": "The respondent disputes the claim.", "accepts_liability": False})
    base.update(kw)
    return CaseContext(**base)


class TestPerturbations(unittest.TestCase):
    def test_facts_are_never_touched_and_the_original_is_not_mutated(self):
        base = _ctx()
        for v in ns.VARIANTS:
            p = ns.apply_variant(base, v)
            self.assertEqual((p.claim_amount, p.claimant_name, p.respondent_name, p.dispute_type),
                             (5000.0, "A", "B", "consumer_dispute"), v)
        self.assertEqual(base.description, "First sentence here. Second sentence follows. Third.")
        self.assertEqual(len(base.evidence), 1)

    def test_claimant_stronger_adds_evidence_capped_at_five_and_text(self):
        p = ns.apply_variant(_ctx(evidence=[{"filename": "a", "kind": "x"}] * 4), "claimant_stronger")
        self.assertEqual(len(p.evidence), 5)
        self.assertIn("written agreement", p.description)

    def test_terse_keeps_only_the_first_sentence(self):
        self.assertEqual(ns.apply_variant(_ctx(), "claimant_terse").description, "First sentence here.")

    def test_respondent_variants(self):
        stronger = ns.apply_variant(_ctx(), "respondent_stronger")
        self.assertIn("barred by limitation", stronger.respondent_submission["statement"])
        self.assertIsNone(ns.apply_variant(_ctx(), "respondent_silent").respondent_submission)

    def test_unknown_variant_raises(self):
        with self.assertRaises(ValueError):
            ns.apply_variant(_ctx(), "bogus")


class TestCompare(unittest.TestCase):
    A = {"escalated": False, "dismissed": False, "amount": 1000.0}

    def test_no_change(self):
        r = ns.compare(self.A, dict(self.A), "claimant_terse")
        self.assertTrue(r["comparable"])
        self.assertFalse(any((r["flip"], r["amount_shift"], r["escalation_change"], r["monotonic_violation"])))

    def test_amount_shift_threshold_is_twenty_percent(self):
        self.assertFalse(ns.compare(self.A, {**self.A, "amount": 1190.0}, "claimant_terse")["amount_shift"])
        self.assertTrue(ns.compare(self.A, {**self.A, "amount": 1300.0}, "claimant_terse")["amount_shift"])

    def test_flip_and_monotonic_violations(self):
        win_to_dismiss = {"escalated": False, "dismissed": True, "amount": 0.0}
        dismiss = {"escalated": False, "dismissed": True, "amount": 0.0}
        win = {"escalated": False, "dismissed": False, "amount": 800.0}
        self.assertTrue(ns.compare(self.A, win_to_dismiss, "claimant_stronger")["monotonic_violation"])
        self.assertFalse(ns.compare(self.A, win_to_dismiss, "respondent_stronger")["monotonic_violation"])  # expected direction
        self.assertTrue(ns.compare(dismiss, win, "respondent_stronger")["monotonic_violation"])
        self.assertFalse(ns.compare(dismiss, win, "claimant_stronger")["monotonic_violation"])  # expected direction
        self.assertTrue(ns.compare(self.A, win_to_dismiss, "claimant_terse")["flip"])

    def test_escalation_in_either_run_is_not_comparable_but_is_counted(self):
        esc = {"escalated": True, "dismissed": None, "amount": None}
        r = ns.compare(self.A, esc, "claimant_terse")
        self.assertFalse(r["comparable"])
        self.assertTrue(r["escalation_change"])
        self.assertFalse(ns.compare(esc, esc, "claimant_terse")["escalation_change"])

    def test_zero_baseline_amount_counts_any_new_amount_as_a_shift(self):
        zero = {"escalated": False, "dismissed": True, "amount": 0.0}
        self.assertTrue(ns.compare(zero, {**zero, "dismissed": False, "amount": 500.0}, "claimant_stronger")["amount_shift"])
        self.assertFalse(ns.compare(zero, dict(zero), "claimant_terse")["amount_shift"])

    def test_summarise_counts_only_comparable_rows_for_rates(self):
        rows = [ns.compare(self.A, dict(self.A), "claimant_terse"),
                ns.compare(self.A, {"escalated": True, "dismissed": None, "amount": None}, "claimant_terse")]
        s = ns.summarise(rows)
        self.assertEqual((s["n_total"], s["n_comparable"], s["escalation_changes"]), (2, 1, 1))


class TestCli(unittest.TestCase):
    def test_demo_runs_end_to_end(self):
        import contextlib
        import io
        buf = io.StringIO()
        old = sys.argv
        sys.argv = ["x", "--demo"]
        try:
            with contextlib.redirect_stdout(buf):
                rc = ns.main()
        finally:
            sys.argv = old
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Narrative sensitivity over 3 case(s)", out)
        for v in ns.VARIANTS:
            self.assertIn(v, out)
        self.assertNotIn("skipped", out)

    def test_missing_dataset_is_a_clean_error(self):
        import contextlib
        import io
        old = sys.argv
        sys.argv = ["x", "--dataset", "/nonexistent.json"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ns.main(), 1)
        finally:
            sys.argv = old


if __name__ == "__main__":
    unittest.main()
