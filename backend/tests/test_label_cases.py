"""Tests for the terminal labeller: resume, undo, the conditional third question, unsure notes,
blindness (never reads the key), and that its output is what judge_human_agreement reads."""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import judge_human_agreement as jha  # noqa: E402
from scripts import label_cases as lc  # noqa: E402

SHEET_COLS = ["case_id", "facts", "REAL_COURT_DECIDED", "AI_DECIDED_relief_type", "AI_DECIDED_amount",
              "AI_DECIDED_order", "claimant_prevailed_ai", "claimant_prevailed_real", "relief_similar"]


def _sheet(path: Path, n: int = 3) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLS)
        w.writeheader()
        for k in range(1, n + 1):
            w.writerow({"case_id": f"c{k}", "facts": f"facts {k} ₹ हिन्दी", "REAL_COURT_DECIDED": "real",
                        "AI_DECIDED_relief_type": "full_refund", "AI_DECIDED_amount": "Rs. 100",
                        "AI_DECIDED_order": "order", "claimant_prevailed_ai": "", "claimant_prevailed_real": "",
                        "relief_similar": ""})


def _feed(answers):
    it = iter(answers)

    def f(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return f


class TestLabeller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sheet, self.out = self.dir / "sheet.csv", self.dir / "labels_you.csv"
        _sheet(self.sheet)
        self.log: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, answers, **kw):
        return lc.run(self.sheet, self.out, input_fn=_feed(answers), out=self.log.append, width=80, **kw)

    def _rows(self):
        with open(self.out, encoding="utf-8-sig", newline="") as f:
            return {r["case_id"]: r for r in csv.DictReader(f)}

    def test_full_run_third_question_only_when_both_won(self):
        # c1: y,y,then q3=y ; c2: y,n (no q3) ; c3: n,n (no q3)
        self._run(["y", "y", "y", "y", "n", "n", "n"])
        rows = self._rows()
        self.assertEqual((rows["c1"]["claimant_prevailed_ai"], rows["c1"]["claimant_prevailed_real"], rows["c1"]["relief_similar"]), ("y", "y", "y"))
        self.assertEqual(rows["c2"]["relief_similar"], "")
        self.assertEqual((rows["c3"]["claimant_prevailed_ai"], rows["c3"]["claimant_prevailed_real"]), ("n", "n"))

    def test_quit_saves_and_rerun_resumes_at_first_unlabelled(self):
        self._run(["y", "n", "q"])  # c1 done, quit on c2 Q1
        self.assertEqual(self._rows()["c1"]["claimant_prevailed_real"], "n")
        self.assertEqual(self._rows()["c2"]["claimant_prevailed_ai"], "")
        self.log.clear()
        self._run(["n", "n"])  # resumes at c2 (then EOF during c3)
        rows = self._rows()
        self.assertEqual(rows["c2"]["claimant_prevailed_ai"], "n")
        self.assertTrue(any("CASE 2 of 3" in line for line in self.log))
        self.assertEqual(rows["c1"]["claimant_prevailed_real"], "n")  # earlier work untouched

    def test_b_at_q2_redoes_the_case_and_b_at_q1_goes_to_previous(self):
        self._run(["y", "y", "y", "n", "b", "n", "n", "q"])  # c1 y,y,y; c2: q1=n then b at q2 -> redo c2: n,n; quit at c3
        rows = self._rows()
        self.assertEqual((rows["c2"]["claimant_prevailed_ai"], rows["c2"]["claimant_prevailed_real"]), ("n", "n"))
        self.log.clear()
        # b at Q1 of c3 goes back to c2, which is relabelled
        self._run(["b", "y", "n", "q"], goto=3)
        self.assertEqual((self._rows()["c2"]["claimant_prevailed_ai"], self._rows()["c2"]["claimant_prevailed_real"]), ("y", "n"))

    def test_unsure_asks_for_a_note_and_is_dropped_by_the_scorer(self):
        self._run(["?", "y", "withdrawn, no merits order", "q"])
        row = self._rows()["c1"]
        self.assertEqual(row["claimant_prevailed_ai"], "?")
        self.assertEqual(row["notes"], "withdrawn, no merits order")
        labels = jha.read_labels(str(self.out))
        self.assertIsNone(labels["c1"]["ai"])
        self.assertIsNone(labels["c1"]["verdict"])

    def test_invalid_input_is_reprompted_and_help_does_not_consume_an_answer(self):
        self._run(["maybe", "h", "y", "y", "n", "q"])
        self.assertTrue(any("Please answer" in line for line in self.log))
        self.assertTrue(any("claimant  = the party" in line for line in self.log))
        self.assertEqual(self._rows()["c1"]["claimant_prevailed_ai"], "y")

    def test_output_is_read_by_the_agreement_script_and_keeps_unicode(self):
        self._run(["y", "y", "y", "y", "n", "n", "n"])
        labels = jha.read_labels(str(self.out))
        self.assertEqual(labels["c1"]["verdict"], "match")
        self.assertEqual(labels["c2"]["verdict"], "mismatch")
        self.assertIsNone(labels["c3"]["verdict"])  # both lost: not comparable with the judge's call
        self.assertIn("₹", self._rows()["c1"]["facts"])

    def test_labeller_code_never_references_the_judge_key(self):
        import inspect
        code = inspect.getsource(lc.run) + inspect.getsource(lc.main) + inspect.getsource(lc.read_sheet)
        self.assertNotIn("human_label_key", code)
        self.assertNotIn("json", code)

    def test_seconds_are_recorded_and_capped(self):
        ticks = iter([0, 5000, 5000, 5001, 5001, 5002, 5002, 5003])
        lc.run(self.sheet, self.out, input_fn=_feed(["y", "n", "q"]), out=self.log.append, width=80, clock=lambda: next(ticks))
        self.assertEqual(self._rows()["c1"]["seconds"], str(lc.MAX_SECONDS_PER_CASE))


if __name__ == "__main__":
    unittest.main()
