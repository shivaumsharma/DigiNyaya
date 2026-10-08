"""The held-out / precedent-source split must be deterministic, disjoint, stratified, stable when cases are added,
and must refuse to silently overwrite (moving cases afterwards would re-introduce contamination)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import split_eval_for_precedents as sp  # noqa: E402

CATS = ("tenancy_disputes", "employment_disputes", "property_neighbor_disputes")


def _cases(n_per=200, extra_cat="consumer_complaints"):
    out = []
    for cat in (*CATS, extra_cat):
        out += [{"case_id": f"{cat[:4]}-{i}", "category": cat} for i in range(n_per)]
    return out


class TestSplit(unittest.TestCase):
    def test_disjoint_complete_and_other_categories_untouched(self):
        cases = _cases()
        held, src = sp.split_cases(cases, CATS, 0.3, seed=1)
        self.assertFalse({c["case_id"] for c in held} & {c["case_id"] for c in src})
        self.assertEqual(len(held) + len(src), len(cases))
        self.assertTrue(all(c["category"] in CATS for c in src))
        self.assertEqual(sum(1 for c in held if c["category"] == "consumer_complaints"), 200)

    def test_each_named_category_contributes_roughly_the_fraction(self):
        _, src = sp.split_cases(_cases(2000), CATS, 0.3, seed=1)
        for cat in CATS:
            n = sum(1 for c in src if c["category"] == cat)
            self.assertTrue(540 <= n <= 660, (cat, n))  # 30% of 2000 = 600, binomial sd ~ 20

    def test_adding_cases_never_moves_an_existing_case(self):
        base = _cases(300)
        more = base + [{"case_id": f"new-{i}", "category": CATS[i % 3]} for i in range(500)]
        _, src_base = sp.split_cases(base, CATS, 0.3, seed=9)
        _, src_more = sp.split_cases(more, CATS, 0.3, seed=9)
        self.assertTrue({c["case_id"] for c in src_base} <= {c["case_id"] for c in src_more})
        held_base = {c["case_id"] for c in sp.split_cases(base, CATS, 0.3, seed=9)[0]}
        held_more = {c["case_id"] for c in sp.split_cases(more, CATS, 0.3, seed=9)[0]}
        self.assertTrue(held_base <= held_more)  # nothing that was held out ever becomes a source case

    def test_deterministic_and_order_independent(self):
        cases = _cases()
        a = sp.split_cases(cases, CATS, 0.3, seed=7)
        b = sp.split_cases(list(reversed(cases)), CATS, 0.3, seed=7)
        self.assertEqual({c["case_id"] for c in a[1]}, {c["case_id"] for c in b[1]})

    def test_a_different_seed_gives_a_different_split(self):
        cases = _cases()
        a = {c["case_id"] for c in sp.split_cases(cases, CATS, 0.3, seed=1)[1]}
        b = {c["case_id"] for c in sp.split_cases(cases, CATS, 0.3, seed=2)[1]}
        self.assertNotEqual(a, b)

    def test_fraction_extremes(self):
        cases = _cases()
        self.assertEqual(sp.split_cases(cases, CATS, 0.0, seed=1)[1], [])
        self.assertEqual(len(sp.split_cases(cases, CATS, 1.0, seed=1)[1]), 600)

    def test_bucket_is_stable_and_in_range(self):
        self.assertEqual(sp.bucket(1, "x"), sp.bucket(1, "x"))
        self.assertTrue(all(0.0 <= sp.bucket(3, f"c{i}") < 1.0 for i in range(500)))


class TestCli(unittest.TestCase):
    def _run(self, argv):
        old = sys.argv
        sys.argv = ["x"] + argv
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                return sp.main()
        finally:
            sys.argv = old

    def test_writes_files_and_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "in.json").write_text(json.dumps(_cases()), encoding="utf-8")
            args = ["--input", str(d / "in.json"), "--heldout-out", str(d / "h.json"), "--source-out", str(d / "s.json")]
            self.assertEqual(self._run(args), 0)
            first = (d / "s.json").read_text(encoding="utf-8")
            manifest = json.loads((d / "h.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["n_input"], 800)
            self.assertEqual(self._run(args), 1)  # refuses
            self.assertEqual((d / "s.json").read_text(encoding="utf-8"), first)  # untouched
            self.assertEqual(self._run(args + ["--force"]), 0)

    def test_missing_input_is_a_clean_error(self):
        self.assertEqual(self._run(["--input", "/nonexistent.json"]), 1)


if __name__ == "__main__":
    unittest.main()
