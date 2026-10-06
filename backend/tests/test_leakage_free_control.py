"""Tests for the leakage-free control experiment: the pre-decision cut never lets the decision through, the sample is
deterministic and stratified, case construction retries/excludes leaky descriptions, the signal prompt refactor is
faithful, and the paired report distinguishes a real drop from no difference."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import build_leakage_free_control as bl  # noqa: E402
from scripts import leakfree_control_report as rep  # noqa: E402

FACTS = ("The plaintiff lent Rs. 1,50,000 to the defendant on 5 March 2019 against a promissory note and the defendant "
         "promised repayment within six months. The plaintiff issued a notice and the defendant replied that the loan "
         "was a gift. Issues were framed on whether the loan was proved. ") * 6
DECISION = "In the result, the suit is hereby decreed with costs and the defendant shall pay Rs. 1,50,000."
TAIL = " Costs as per the rules and the court fee shall be refunded to the plaintiff." * 40


def _case(cid="IK-EVAL-1", cat="small_claims_debt_recovery", **kw):
    base = {"case_id": cid, "category": cat, "case_description": "Original description.", "expected_outcome": "Decreed.",
            "source": {"docid": cid.split("-")[-1]}, "signals": {"claimant_evidence_count": 3}}
    base.update(kw)
    return base


class TestPreDecisionCut(unittest.TestCase):
    def test_the_decision_never_survives_a_marker_cut(self):
        r = bl.pre_decision_text(FACTS[:1000] + " " + DECISION + TAIL)
        self.assertIsNotNone(r)
        self.assertNotIn("decreed", r["text"].lower())
        self.assertNotIn("in the result", r["text"].lower())
        self.assertTrue(r["reason"].startswith("marker"))

    def test_the_fraction_cap_is_a_second_safety_net_when_the_marker_comes_late(self):
        r = bl.pre_decision_text(FACTS + DECISION)  # decision at ~90% of the text, beyond the 60% cap
        self.assertTrue(r["reason"].startswith("fraction"))
        self.assertNotIn("decreed", r["text"].lower())

    def test_the_cut_ends_on_a_complete_sentence(self):
        text = "The tenant stopped paying rent in January. " * 40 + "In the result the suit" + " is decreed." * 40
        r = bl.pre_decision_text(text.replace("In the result", "Inthe result"), max_fraction=0.5)  # marker deliberately NOT matchable
        self.assertTrue(r["text"].endswith("."))
        self.assertNotIn("Inthe", r["text"][-30:])

    def test_without_a_marker_the_fraction_cap_applies(self):
        text = ("The parties are neighbours and disagree about a boundary wall. " * 60)
        r = bl.pre_decision_text(text, max_fraction=0.5)
        self.assertEqual(r["reason"], "fraction 0.5")
        self.assertLessEqual(r["kept_chars"], int(len(text) * 0.5))

    def test_a_marker_inside_the_header_is_ignored(self):
        text = "IN THE RESULT of the earlier order, header only. " + ("Facts about a tenancy dispute and rent. " * 80)
        r = bl.pre_decision_text(text)
        self.assertTrue(r["reason"].startswith("fraction"))

    def test_too_short_is_skipped_never_padded(self):
        self.assertIsNone(bl.pre_decision_text("short"))
        self.assertIsNone(bl.pre_decision_text("x" * 700 + " In the result " + "y" * 50, max_fraction=0.1))
        self.assertIsNone(bl.pre_decision_text(None))

    def test_the_earliest_marker_wins(self):
        text = FACTS[:1000] + " In view of the above, the court considered the evidence. " + "More reasoning. " * 20 + DECISION + TAIL
        r = bl.pre_decision_text(text)
        self.assertIn("in view of the above", r["reason"])


class TestSampling(unittest.TestCase):
    def _data(self):
        cases, verdicts = [], {}
        for i in range(400):
            cat = ("small_claims_debt_recovery", "tenancy_disputes")[i % 2]
            won = (i % 7) != 0
            cid = f"IK-EVAL-{i}"
            cases.append(_case(cid, cat))
            verdicts[cid] = {"verdict": "match", "claimant_prevailed_real": won}
        cases.append(_case("IK-EVAL-unjudged"))
        return cases, verdicts

    def test_deterministic_judged_only_and_sized(self):
        cases, verdicts = self._data()
        a = bl.select_sample(cases, verdicts, 60, seed=3)
        b = bl.select_sample(list(reversed(cases)), verdicts, 60, seed=3)
        self.assertEqual({c["case_id"] for c in a}, {c["case_id"] for c in b})
        self.assertLessEqual(len(a), 60)
        self.assertNotIn("IK-EVAL-unjudged", {c["case_id"] for c in a})

    def test_both_outcomes_and_both_categories_are_represented(self):
        cases, verdicts = self._data()
        s = bl.select_sample(cases, verdicts, 60, seed=3)
        self.assertEqual({c["category"] for c in s}, {"small_claims_debt_recovery", "tenancy_disputes"})
        self.assertEqual({bool(verdicts[c["case_id"]]["claimant_prevailed_real"]) for c in s}, {True, False})

    def test_empty_and_zero(self):
        self.assertEqual(bl.select_sample([], {}, 10, 1), [])
        cases, verdicts = self._data()
        self.assertEqual(bl.select_sample(cases, verdicts, 0, 1), [])


class FakeLLM:
    def __init__(self, descriptions, signals=None):
        self.descriptions, self.signals, self.prompts = list(descriptions), signals if signals is not None else {"claimant_evidence_count": 2}, []

    def __call__(self, prompt, system=None):
        self.prompts.append(prompt)
        if "JUDGMENT (BEFORE THE DECISION)" in prompt:
            d = self.descriptions.pop(0)
            return None if d is None else {"case_description": d}
        return self.signals


CLEAN = "The claimant alleges the respondent borrowed money against a note and has not repaid it, which the respondent contends was a gift."


class TestBuildOne(unittest.TestCase):
    RAW = FACTS + DECISION

    def test_success_replaces_inputs_and_keeps_the_rest(self):
        fake = FakeLLM([CLEAN])
        status, ctl, info = bl.build_one(_case(), self.RAW, max_fraction=0.6, generate=fake)
        self.assertEqual(status, "ok")
        self.assertEqual(ctl["case_description"], CLEAN)
        self.assertEqual(ctl["signals"], {"claimant_evidence_count": 2})
        self.assertEqual((ctl["expected_outcome"], ctl["category"], ctl["case_id"]), ("Decreed.", "small_claims_debt_recovery", "IK-EVAL-1"))
        self.assertEqual(ctl["control"]["original_description"], "Original description.")
        self.assertTrue(ctl["control"]["had_original_signals"])

    def test_no_prompt_ever_contains_the_decision(self):
        fake = FakeLLM([CLEAN])
        bl.build_one(_case(), self.RAW, max_fraction=0.6, generate=fake)
        self.assertGreaterEqual(len(fake.prompts), 2)  # description + signals
        for p in fake.prompts:
            self.assertNotIn("hereby decreed", p)
            self.assertNotIn("In the result", p)

    def test_a_leaky_first_answer_is_retried_with_the_strict_instruction(self):
        fake = FakeLLM(["The court decreed the suit for the plaintiff.", CLEAN])
        status, ctl, info = bl.build_one(_case(), self.RAW, max_fraction=0.6, generate=fake)
        self.assertEqual(status, "ok")
        self.assertEqual(info["retries"], 1)
        self.assertIn("previous answer contained wording", fake.prompts[1])

    def test_still_leaky_after_the_retry_is_excluded_not_kept(self):
        leaky = "The court decreed the suit for the plaintiff."
        status, ctl, info = bl.build_one(_case(), self.RAW, max_fraction=0.6, generate=FakeLLM([leaky, leaky]))
        self.assertEqual((status, ctl), ("leak_excluded", None))
        self.assertTrue(info["leak_flags"])

    def test_a_named_judge_counts_as_leaky(self):
        self.assertIn("names a judge", bl.description_leaks("The matter came before Hon'ble Justice Rao on a loan claim."))
        self.assertEqual(bl.description_leaks(CLEAN), [])

    def test_missing_text_short_text_and_llm_failure(self):
        self.assertEqual(bl.build_one(_case(), None, max_fraction=0.6, generate=FakeLLM([]))[0], "no_text")
        self.assertEqual(bl.build_one(_case(), "tiny", max_fraction=0.6, generate=FakeLLM([]))[0], "too_short")
        self.assertEqual(bl.build_one(_case(), self.RAW, max_fraction=0.6, generate=FakeLLM([None]))[0], "llm_fail")

    def test_signal_prompt_refactor_embeds_the_given_text(self):
        from scripts.extract_case_signals import build_signals_prompt
        p = build_signals_prompt("MY TEXT")
        self.assertTrue(p.endswith("JUDGMENT TEXT:\nMY TEXT"))
        self.assertIn("FACTS AND ARGUMENTS ONLY", p)


class TestLoadAndCli(unittest.TestCase):
    def test_load_raw_text_reads_and_strips_html(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "9.json").write_text(json.dumps({"doc": "<p>Hello &amp; <b>world</b></p>"}), encoding="utf-8")
            self.assertEqual(bl.load_raw_text(_case("IK-EVAL-9"), Path(d)), "Hello & world")
            self.assertIsNone(bl.load_raw_text(_case("IK-EVAL-404"), Path(d)))

    def test_cli_end_to_end_resumes_without_repeating_calls(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache = d / "cache"
            cache.mkdir()
            cases, verdicts = [], []
            for i in range(6):
                cid = f"IK-EVAL-{i}"
                cases.append(_case(cid))
                verdicts.append({"case_id": cid, "verdict": "match", "claimant_prevailed_real": i % 2 == 0, "category": "small_claims_debt_recovery"})
                (cache / f"{i}.json").write_text(json.dumps({"doc": FACTS + DECISION}), encoding="utf-8")
            (d / "ds.json").write_text(json.dumps(cases), encoding="utf-8")
            (d / "v.json").write_text(json.dumps(verdicts), encoding="utf-8")
            calls = []

            def fake(prompt, system=None, **kw):
                calls.append(prompt)
                return {"case_description": CLEAN} if "BEFORE THE DECISION" in prompt and "Return JSON only: {\"case_description\"" in prompt else {"claimant_evidence_count": 2}

            argv = ["x", "--dataset", str(d / "ds.json"), "--verdicts", str(d / "v.json"), "--n", "6",
                    "--out-dir", str(d), "--cache-dir", str(cache)]
            old = sys.argv
            try:
                with patch("app.llm.is_available", return_value=True), patch.object(bl, "_generate_json", side_effect=fake):
                    import contextlib
                    import io
                    sys.argv = argv
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(bl.main(), 0)
                    first_calls = len(calls)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(bl.main(), 0)
            finally:
                sys.argv = old
            self.assertEqual(len(calls), first_calls)  # second run: everything already done
            ctl = json.loads((d / "eval_leakfree_control.json").read_text(encoding="utf-8"))
            orig = json.loads((d / "eval_leakfree_original.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ctl), 6)
            self.assertEqual({c["case_id"] for c in ctl}, {c["case_id"] for c in orig})
            self.assertTrue(all(c["case_description"] == CLEAN for c in ctl))
            self.assertTrue(all(c["case_description"] == "Original description." for c in orig))
            report = json.loads((d / "leakfree_control_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], {"ok": 6})


def _row(cid, ai, real, amount_ok=True):
    return {"case_id": cid, "category": "small_claims_debt_recovery", "claimant_prevailed_ai": ai, "claimant_prevailed_real": real,
            "verdict": "match" if (ai == real and amount_ok) else ("partial" if ai == real else "mismatch"), "ai_amount": 50000.0}


class TestReport(unittest.TestCase):
    def _dataset(self, ids):
        return {i: {"case_id": i, "expected_outcome": "The court decreed Rs. 50,000.", "category": "small_claims_debt_recovery"} for i in ids}

    def test_paired_stats_sign_and_mcnemar(self):
        s = rep.paired_stats([(1, 0)] * 40 + [(0, 1)] * 5 + [(1, 1)] * 55)
        self.assertEqual((s["control_worse"], s["control_better"]), (40, 5))
        self.assertAlmostEqual(s["diff"], -0.35)
        self.assertLess(s["mcnemar_p"], 1e-6)
        self.assertLess(s["ci95"][1], 0)

    def test_a_real_drop_is_called_inflation(self):
        ids = [f"c{i}" for i in range(200)]
        orig = [_row(i, True, True) for i in ids]
        ctl = [_row(i, False, True) if k < 80 else _row(i, True, True) for k, i in enumerate(ids)]
        res = rep.compare_runs(orig, ctl, self._dataset(ids))
        self.assertEqual(res["n_common"], 200)
        self.assertLess(res["winner_accuracy"]["diff"], 0)
        self.assertIn("LOWER", rep.interpret("Winner accuracy", res["winner_accuracy"]))
        self.assertEqual(res["direction_changed"], 80)

    def test_identical_runs_show_no_difference_and_state_the_detectable_size(self):
        ids = [f"c{i}" for i in range(150)]
        rows = [_row(i, k % 3 != 0, True) for k, i in enumerate(ids)]
        res = rep.compare_runs(rows, [dict(r) for r in rows], self._dataset(ids))
        self.assertEqual(res["winner_accuracy"]["diff"], 0)
        text = rep.interpret("Full match", res["full_match"])
        self.assertIn("no detectable difference", text)
        self.assertIn("unlikely at this sample size", text)

    def test_only_cases_judged_in_both_runs_are_compared(self):
        ids = [f"c{i}" for i in range(10)]
        orig = [_row(i, True, True) for i in ids]
        ctl = [_row(i, True, True) for i in ids[:6]] + [{"case_id": "x1", "verdict": "judge_failed"}]
        res = rep.compare_runs(orig, ctl, self._dataset(ids))
        self.assertEqual((res["n_common"], res["n_original_only"]), (6, 4))

    def test_no_overlap_returns_an_empty_result(self):
        res = rep.compare_runs([_row("a", True, True)], [_row("b", True, True)], self._dataset(["a", "b"]))
        self.assertEqual(res["n_common"], 0)

    def test_cli_runs_end_to_end_and_reports_missing_files_cleanly(self):
        import contextlib
        import io
        ids = [f"IK-EVAL-{i}" for i in range(80)]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ctl_cases = [_case(i, case_description="Control text.", expected_outcome="The court decreed Rs. 50,000.") for i in ids]
            (d / "cc.json").write_text(json.dumps(ctl_cases), encoding="utf-8")
            (d / "oc.json").write_text(json.dumps([_case(i) for i in ids]), encoding="utf-8")
            (d / "ov.json").write_text(json.dumps([_row(i, True, True) for i in ids]), encoding="utf-8")
            (d / "cv.json").write_text(json.dumps([_row(i, k % 4 != 0, True) for k, i in enumerate(ids)]), encoding="utf-8")
            argv = ["x", "--original-verdicts", str(d / "ov.json"), "--control-verdicts", str(d / "cv.json"),
                    "--control-cases", str(d / "cc.json"), "--original-cases", str(d / "oc.json")]
            buf, old = io.StringIO(), sys.argv
            try:
                sys.argv = argv
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(rep.main(), 0)
                out = buf.getvalue()
                self.assertIn("Cases judged in BOTH runs: 80", out)
                self.assertIn("Winner accuracy: control is LOWER", out)
                self.assertIn("Text-only baseline", out)
                sys.argv = ["x", "--original-verdicts", str(d / "nope.json"), "--control-verdicts", str(d / "cv.json"),
                            "--control-cases", str(d / "cc.json"), "--original-cases", str(d / "oc.json")]
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    self.assertEqual(rep.main(), 1)
                self.assertIn("Not found", buf2.getvalue())
            finally:
                sys.argv = old


if __name__ == "__main__":
    unittest.main()
