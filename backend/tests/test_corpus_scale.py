"""Tests for the corpus-scaling tools: scripts.district_supply_census, scripts.plan_corpus_scale, scripts.corpus_gate."""
from __future__ import annotations

import json
import os
import random
import string
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from scripts import corpus_gate as g  # noqa: E402
from scripts import district_supply_census as census  # noqa: E402
from scripts import plan_corpus_scale as plan  # noqa: E402

CATS = {"contract_disputes", "small_claims_debt_recovery"}

BASE = ("The plaintiff, a small trader in Guntur, supplied rice stock to the defendant on credit between March and June "
        "2019 under oral orders confirmed by invoices. The defendant paid part of the price and acknowledged the balance of "
        "Rs. 3,40,000 in a letter dated 4 January 2020 but did not pay. The plaintiff issued a notice and filed a suit "
        "for recovery with interest. The defendant says the goods were substandard and that part payment was made in cash.")


def _filler(i: int, n: int = 250) -> str:
    """Distinct pseudo-text per case, so unrelated test cases are not near-duplicates of each other."""
    rng = random.Random(i)
    return " ".join("".join(rng.choices(string.ascii_lowercase, k=6)) for _ in range(n))


def case(i: int, **kw) -> dict:
    c = {"case_id": f"DC-EVAL-{i}", "category": "small_claims_debt_recovery", "language": "en",
         "case_description": BASE.replace("Guntur", f"Town{i}") + " " + _filler(i),
         "expected_outcome": "The suit was partly upheld and the defendant must pay Rs. 2,10,000 with interest.",
         "source": {"docid": f"CINO{i}", "title": "OS 1/2020", "court": f"Court {i % 40}, District, Andhra Pradesh", "year": 2022},
         "verified": False}
    c.update(kw)
    return c


class AllocateTests(unittest.TestCase):
    def test_proportional_when_uncapped(self):
        a = plan.allocate({"x": 300, "y": 100}, 200, 1.0)
        self.assertEqual(a, {"x": 150, "y": 50})

    def test_cap_redistributes_the_excess(self):
        a = plan.allocate({"x": 900, "y": 100, "z": 100}, 300, 0.5)
        self.assertEqual(a["x"], 150)               # held to cap_share x total
        self.assertEqual(a["y"], 75)
        self.assertEqual(a["z"], 75)
        self.assertGreaterEqual(sum(a.values()), 299)

    def test_never_exceeds_capacity_and_reports_shortfall(self):
        a = plan.allocate({"x": 10, "y": 5}, 100, 1.0)
        self.assertEqual(a, {"x": 10, "y": 5})

    def test_infeasible_cap_falls_short_instead_of_breaking_it(self):
        a = plan.allocate({"x": 1000}, 100, 0.4)
        self.assertEqual(a, {"x": 40})


class PlanTests(unittest.TestCase):
    def groups(self):
        out = []
        for st, name, n in (("2", "Andhra Pradesh", 4000), ("29", "Telangana", 1500)):
            for y in ("2022", "2023", "2024"):
                for k in range(12):
                    out.append({"year": y, "state": st, "state_name": name, "district": "1", "complex": str(k),
                                "candidates": n // 12, "tar_bytes": 5_000_000})
        out.append({"year": "2024", "state": "7", "state_name": "Delhi", "district": "1", "complex": "1",
                    "candidates": 9999, "tar_bytes": 0})  # metadata only: must never be planned
        return out

    def test_missing_archives_are_never_planned(self):
        p = plan.build_plan(self.groups(), 1000, 0.5)
        self.assertNotIn("7", p["state_totals"])

    def test_state_share_cap_holds(self):
        p = plan.build_plan(self.groups(), 1000, 0.5, max_state_share=0.6, max_year_share=1.0)
        self.assertLessEqual(p["state_totals"]["2"], 600)
        self.assertGreaterEqual(p["state_totals"]["29"], 390)

    def test_shortfall_reported_when_supply_cannot_reach_target(self):
        p = plan.build_plan(self.groups(), 10**6, 0.5)
        self.assertGreater(p["shortfall"], 0)
        self.assertEqual(p["planned"] + p["shortfall"], p["target"])

    def test_min_year_excludes_old_groups(self):
        p = plan.build_plan(self.groups(), 500, 0.5, min_year=2024)
        self.assertEqual({r["year"] for r in p["rows"]}, {"2024"})

    def test_complexes_needed_visits_largest_first_and_scales_with_yield(self):
        self.assertEqual(plan.complexes_needed([100, 100, 100, 100], 50, 0.5, 25, safety=1.0), 2)
        self.assertGreater(plan.complexes_needed([100] * 40, 50, 0.1, 25), plan.complexes_needed([100] * 40, 50, 0.5, 25))

    def test_command_points_to_its_own_slice_file(self):
        r = {"state": "2", "year": "2023", "quota": 500, "complexes": 30}
        cmd = plan.command_for(r, 25)
        self.assertIn("--states 2 --years 2023 --target 500 --max-complexes 30", cmd)
        self.assertIn("dc_2_2023.json", cmd)
        self.assertIn("--exclude-from", cmd)   # slices must not re-source cases already in the canonical file

    def test_pilot_ignores_states_with_too_few_courts_to_say_anything(self):
        g2 = self.groups() + [{"year": "2024", "state": "8", "state_name": "OneCourt", "district": "1", "complex": "1",
                               "candidates": 10**6, "tar_bytes": 9}]
        self.assertNotIn("--states 8", " ".join(plan.pilot_commands(g2, 150)))

    def test_pilot_spreads_across_states(self):
        cmds = plan.pilot_commands(self.groups(), 150)
        states = {c.split("--states ")[1].split()[0] for c in cmds}
        self.assertEqual(states, {"2", "29"})
        self.assertNotIn("--states 7", " ".join(cmds))


class CensusTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({
            "case_type": ["CS", "OS", "cs (comm)", "CRL", "CS"], "has_pdf": [True, True, True, True, False],
            "final_orders": [1, 2, 1, 1, 1], "state_name": ["S"] * 5,
            "district_code": ["1", "1", "2", "2", "2"], "complex_code": ["a", "a", "b", "b", "b"]})

    def test_filter_matches_the_sourcing_scripts_rules(self):
        self.assertEqual(len(census.civil_candidates(self.frame(), "99")), 2)   # CS, CS (COMM); OS only in AP/TG
        self.assertEqual(len(census.civil_candidates(self.frame(), "29")), 3)   # Telangana also counts OS

    def test_groups_and_summary(self):
        cands = census.civil_candidates(self.frame(), "29")
        groups = census.complex_groups(cands, "2023", "29")
        self.assertEqual(sum(x["candidates"] for x in groups), 3)
        groups[0]["tar_bytes"] = 1_000_000
        for x in groups[1:]:
            x["tar_bytes"] = 0
        s = census.summarise(groups)
        self.assertEqual(s["metadata_candidates"], 3)
        self.assertEqual(s["downloadable_candidates"], groups[0]["candidates"])


class RowCheckTests(unittest.TestCase):
    def reasons(self, **kw):
        return g.check_row(case(1, **kw), CATS)

    def test_clean_case_passes(self):
        rej, _ = self.reasons()
        self.assertEqual(rej, [])

    def test_each_reject_reason(self):
        cases = {
            "category_not_mappable": {"category": "nonsense"},
            "description_too_short": {"case_description": "too short"},
            "outcome_too_short": {"expected_outcome": "Dismissed."},
            "not_english": {"case_description": ("వాది ప్రతివాది " * 60)},
            "pii_phone": {"case_description": BASE + " Call 9876543210 for details."},
            "pii_email": {"case_description": BASE + " Mail someone@example.com."},
            "pii_aadhaar": {"case_description": BASE + " Aadhaar 1234 5678 9012."},
            "pii_pan": {"case_description": BASE + " PAN ABCDE1234F."},
            "states_the_decision": {"case_description": BASE + " The suit was decreed with costs."},
            "names_the_judge": {"case_description": BASE + " Presided over by Smt. Rao."},
        }
        for reason, kw in cases.items():
            with self.subTest(reason):
                rej, _ = self.reasons(**kw)
                self.assertIn(reason, rej)

    def test_amounts_are_not_mistaken_for_phone_numbers(self):
        rej, _ = self.reasons(case_description=BASE + " The sum of Rs. 9876543210.50 was claimed.")
        self.assertNotIn("pii_phone", rej)

    def test_malformed_record(self):
        rej, _ = g.check_row({"case_id": "x"}, CATS)
        self.assertEqual(rej, ["malformed"])

    def test_flags_do_not_reject(self):
        rej, flags = self.reasons(case_description=BASE + " Smt. Lakshmi appeared for the plaintiff.")
        self.assertEqual(rej, [])
        self.assertIn("honorific_name_left", flags)


class GateTests(unittest.TestCase):
    def test_exact_duplicate_of_existing_and_within_batch(self):
        existing = [case(1)]
        batch = [case(1), case(2), case(2)]
        res = g.run_gate(batch, existing, set(), CATS)
        self.assertEqual([c["case_id"] for c in res["survivors"]], ["DC-EVAL-2"])
        self.assertEqual(res["reject_reasons"]["duplicate_id"], 2)

    def test_near_duplicate_text_is_caught_but_distinct_text_is_not(self):
        a = case(1)
        b = case(2, case_description=a["case_description"] + " Costs were also claimed.")
        c = case(3, case_description=("A tenant in Hyderabad withheld rent for the shop premises let on a monthly tenancy "
                                      "after the landlord refused repairs to the leaking roof; the landlord sued for arrears "
                                      "and eviction and the tenant pleaded set-off for repair costs and denied the rent rate. ") * 2)
        res = g.run_gate([a, b, c], [], set(), CATS)
        self.assertIn("DC-EVAL-2", res["rejected"])
        self.assertTrue(res["rejected"]["DC-EVAL-2"][0].startswith("near_duplicate_of:DC-EVAL-1"))
        self.assertEqual({x["case_id"] for x in res["survivors"]}, {"DC-EVAL-1", "DC-EVAL-3"})

    def test_precedent_overlap_rejected(self):
        res = g.run_gate([case(5)], [], {"CINO5"}, CATS)
        self.assertIn("in_precedent_corpus", res["rejected"]["DC-EVAL-5"])

    def test_a_rejected_case_does_not_become_the_first_copy_of_a_cluster(self):
        bad = case(1, expected_outcome="no")
        twin = case(2, case_description=bad["case_description"])
        res = g.run_gate([bad, twin], [], set(), CATS)
        self.assertEqual([c["case_id"] for c in res["survivors"]], ["DC-EVAL-2"])


class BatchCheckTests(unittest.TestCase):
    def test_concentration_warnings_and_failure(self):
        survivors = [case(i) for i in range(600)]
        for c in survivors:  # one court holds everything
            c["source"]["court"] = "Only Court, District, Andhra Pradesh"
        checks = {name: status for status, name, _ in g.batch_checks(600, survivors, {})}
        self.assertEqual(checks["largest court share (<=2%)"], "FAIL")
        self.assertEqual(checks["largest state share"], "FAIL")

    def test_diverse_batch_passes_concentration(self):
        survivors = [case(i) for i in range(1000)]
        for i, c in enumerate(survivors):
            c["source"]["court"] = f"Court {i % 80}, D, {['Andhra Pradesh', 'Telangana', 'Bihar'][i % 3]}"
            c["source"]["year"] = 2018 + i % 6
        checks = {name: status for status, name, _ in g.batch_checks(1000, survivors, {})}
        self.assertEqual(checks["largest court share (<=2%)"], "PASS")
        self.assertEqual(checks["largest state share"], "PASS")

    def test_empty_batch_fails(self):
        self.assertEqual(g.batch_checks(10, [], {})[0][0], "FAIL")


if __name__ == "__main__":
    unittest.main()


class PilotExtractabilityTests(unittest.TestCase):
    def setUp(self):
        from scripts import pilot_extractability as pe

        self.pe = pe

    def test_classify_text_statuses(self):
        self.assertEqual(self.pe.classify_text(None)["status"], "no_text_or_member")
        self.assertEqual(self.pe.classify_text("short")["status"], "text_under_200")
        long_text = "The suit is decreed. " + "x " * 200
        row = self.pe.classify_text(long_text)
        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["decision_words"])
        self.assertFalse(row["criminal_terms"])
        self.assertTrue(self.pe.classify_text("The accused sought bail. " + "x " * 200)["criminal_terms"])

    def test_choose_complexes_skips_missing_big_and_thin_archives(self):
        supply = [
            {"state": "2", "year": "2022", "district": "1", "complex": "a", "candidates": 50, "tar_bytes": 10_000_000},
            {"state": "2", "year": "2022", "district": "1", "complex": "b", "candidates": 90, "tar_bytes": 500_000_000},  # too big
            {"state": "2", "year": "2022", "district": "1", "complex": "c", "candidates": 80, "tar_bytes": 0},            # no archive
            {"state": "2", "year": "2022", "district": "1", "complex": "d", "candidates": 5, "tar_bytes": 1_000_000},     # too few
            {"state": "2", "year": "2022", "district": "1", "complex": "e", "candidates": 70, "tar_bytes": 20_000_000},
            {"state": "29", "year": "2022", "district": "1", "complex": "f", "candidates": 99, "tar_bytes": 1_000_000},   # other state
        ]
        got = self.pe.choose_complexes(supply, "2", "2022", max_tar_mb=60, count=5)
        self.assertEqual([g["complex"] for g in got], ["e", "a"])

    def test_summarise(self):
        rows = [
            {"state": "2", "year": "2022", "status": "ok", "chars": 5000, "decision_words": True, "criminal_terms": False},
            {"state": "2", "year": "2022", "status": "ok", "chars": 1000, "decision_words": False, "criminal_terms": True},
            {"state": "2", "year": "2022", "status": "no_text_or_member", "chars": 0, "decision_words": False, "criminal_terms": False},
            {"state": "29", "year": "2022", "status": "ok", "chars": 3000, "decision_words": True, "criminal_terms": False},
        ]
        s = self.pe.summarise(rows)
        self.assertEqual((s["attempted"], s["extractable"]), (4, 3))
        self.assertAlmostEqual(s["extractable_rate"], 0.75)
        self.assertAlmostEqual(s["by_slice"]["2/2022"]["extractable_rate"], 2 / 3)
        self.assertAlmostEqual(s["short_order_share"], 1 / 3)
        self.assertAlmostEqual(s["decision_words_share"], 2 / 3)
        self.assertEqual(s["by_status"]["no_text_or_member"], 1)

    def test_summarise_with_nothing_extractable(self):
        s = self.pe.summarise([{"state": "2", "year": "2022", "status": "no_text_or_member", "chars": 0,
                                "decision_words": False, "criminal_terms": False}])
        self.assertEqual(s["extractable"], 0)
        self.assertNotIn("chars", s)

    def test_parse_slices(self):
        self.assertEqual(self.pe._parse_slices("2:2022, 29:2023"), [("2", "2022"), ("29", "2023")])


class SourcingHarness(unittest.TestCase):
    """main() of the district-court sourcing script with the network and LLM mocked out. Holds no tests itself."""

    def setUp(self):
        import tempfile
        from unittest import mock

        from scripts import source_district_court_judgments as m

        self.m, self.mock = m, mock
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "slice.json"
        self.calls: list[str] = []
        rows = [{"cino": f"CINO{i}", "case_no": str(i), "case_type": "OS", "has_pdf": True, "final_orders": 1,
                 "district_code": "1", "complex_code": "1", "state_name": "S", "state_code": "2", "reg_year": 2020,
                 "complex_name": "C", "district_name": "D"} for i in range(1, 7)]
        self.meta = pd.DataFrame(rows)
        self.patches = [
            mock.patch.object(m.llm, "is_available", lambda: True),
            mock.patch.object(m, "get_s3_client", lambda: None),
            mock.patch.object(m, "load_metadata", lambda s3, state, year: self.meta),
            mock.patch.object(m, "download_complex_tar", lambda *a, **k: self.tmp / "t.tar"),
            mock.patch.object(m, "extract_case_text", self._text),
            mock.patch.object(m, "extract_and_classify", self._classify),
            mock.patch.object(m, "IK_CACHE_DIR", self.tmp / "ik"),
            mock.patch.object(m, "BAD_COMPLEX_CACHE_PATH", self.tmp / "bad.json"),
        ]
        for p in self.patches:
            p.start()
        self.interrupt_at = None

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _text(self, tar, case_no):
        return None if case_no == "3" else "judgment text " * 40  # case 3 is unreadable

    def _classify(self, text, case_type):
        n = len(self.calls) + 1
        self.calls.append(str(n))
        if self.interrupt_at and n == self.interrupt_at:
            raise KeyboardInterrupt
        accept = n % 2 == 0  # alternate: reject, accept, reject, accept...
        return {"is_genuine_civil_dispute": True, "is_final_merits_decision": accept, "category": "small_claims_debt_recovery",
                "case_description": "d" * 300, "expected_outcome": "o" * 80, "cited_precedent": None}

    def run_main(self, target: int):
        with self.mock.patch("sys.argv", ["x", "--states", "2", "--years", "2022", "--target", str(target),
                                          "--max-complexes", "5", "--max-per-complex", "50", "--out", str(self.out)]):
            return self.m.main()



class SourcingResumeTests(SourcingHarness):
    """Tally, resume and Ctrl+C."""

    def test_tally_and_resume_do_not_repay_for_rejected_cases(self):
        self.assertEqual(self.run_main(target=2), 0)
        # order: case1 rejected, case2 accepted, case3 unreadable, case4 rejected, case5 accepted -> target met
        t = self.m.load_tally(self.out.with_suffix(".tally.json"))
        self.assertEqual((t["added"], t["rejected"], t["extraction_failed"]), (2, 2, 1))
        self.assertEqual(len(self.calls), 4)
        self.run_main(target=2)   # resume: cases 1-5 are accepted, rejected or unreadable; only case 6 is new
        self.assertEqual(len(self.calls), 5)
        t2 = self.m.load_tally(self.out.with_suffix(".tally.json"))
        self.assertEqual((t2["added"], t2["rejected"], t2["extraction_failed"]), (2, 3, 1))

    def test_ctrl_c_saves_progress_and_exits_cleanly(self):
        self.interrupt_at = 3
        self.assertEqual(self.run_main(target=5), 0)
        saved = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 1)                                 # the one accepted before the interrupt
        t = self.m.load_tally(self.out.with_suffix(".tally.json"))
        self.assertEqual(t["added"], 1)

    def test_tally_line_reports_yield(self):
        line = self.m.tally_line({"added": 3, "rejected": 6, "extraction_failed": 1, "classify_failed": 0, "skip_docids": []})
        self.assertIn("attempted=10", line)
        self.assertIn("yield=30.0%", line)
        self.assertIn("yield=n/a", self.m.tally_line({"added": 0, "rejected": 0, "extraction_failed": 0, "classify_failed": 0}))


class DisposalTests(unittest.TestCase):
    def setUp(self):
        from scripts import source_district_court_judgments as m

        self.m = m

    def test_classify_disposal(self):
        pad = "The plaintiff sued for recovery of money lent. " * 30
        cases = {
            "compromise": pad + " The parties filed a joint memo of compromise and the suit is disposed accordingly.",
            "withdrawn": pad + " The plaintiff reported that the suit is not pressed and sought to withdraw.",
            "dismissed_default": pad + " Accordingly the suit is dismissed for default.",
            "ex_parte_decreed": pad + " The defendant remained ex parte. The suit is decreed with costs.",
            "decreed": pad + " After trial the suit is decreed with costs.",
            "dismissed": pad + " In the result the suit is dismissed without costs.",
            "unknown": pad,
        }
        for want, text in cases.items():
            with self.subTest(want):
                self.assertEqual(self.m.classify_disposal(text), want)

    def test_compromise_mentioned_only_early_does_not_hide_a_later_decree(self):
        early = "The defendant said there was no compromise possible. " + "facts " * 900
        self.assertEqual(self.m.classify_disposal(early + " After trial the suit is decreed."), "decreed")

    def test_gate_warns_when_ex_parte_dominates(self):
        survivors = [case(i) for i in range(10)]
        for i, c in enumerate(survivors):
            c["source"]["disposal"] = "ex_parte_decreed" if i < 6 else "decreed"
        res = {name: status for status, name, _ in g.batch_checks(10, survivors, {})}
        self.assertEqual(res["ex parte decrees (defendant never appeared)"], "WARN")


class PrescreenTests(SourcingHarness):
    def _text(self, tar, case_no):
        if case_no == "1":
            return "The matter was settled; a joint memo of compromise was filed. " + "x " * 200
        return super()._text(tar, case_no)

    def test_compromise_case_costs_no_llm_call_and_is_counted(self):
        self.run_main(target=1)
        t = self.m.load_tally(self.out.with_suffix(".tally.json"))
        self.assertEqual(t["prescreened_out"], 1)
        self.assertEqual(len(self.calls), 2)       # case 1 skipped; case 2 rejected (call 1); case 4 accepted? see loop
        self.assertIn("prescreened_out=1", self.m.tally_line(t))
        saved = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIn(saved[0]["source"]["disposal"], {"unknown"})


class TagDisposalTests(unittest.TestCase):
    def test_tags_from_cached_text_and_counts_missing(self):
        import tempfile

        from scripts import tag_disposal as td

        d = Path(tempfile.mkdtemp())
        (d / "A.json").write_text(json.dumps({"doc": "The defendant was ex parte. " + "x " * 100 + " The suit is decreed."}), encoding="utf-8")
        (d / "B.json").write_text(json.dumps({"doc": "x " * 100 + " After trial the suit is decreed."}), encoding="utf-8")
        cases = [{"case_id": "DC-EVAL-A", "category": "c1", "source": {"docid": "A"}},
                 {"case_id": "DC-EVAL-B", "category": "c1", "source": {"docid": "B"}},
                 {"case_id": "DC-EVAL-C", "category": "c2", "source": {"docid": "C"}}]   # no cached text
        out, s = td.tag_cases(cases, d)
        self.assertEqual((s["tagged"], s["no_cached_text"]), (2, 1))
        self.assertEqual(s["counts"], {"ex_parte_decreed": 1, "decreed": 1})
        self.assertAlmostEqual(s["shares"]["ex_parte_decreed"], 0.5)
        self.assertEqual(out[0]["source"]["disposal"], "ex_parte_decreed")
        self.assertNotIn("disposal", out[2]["source"])
        self.assertNotIn("disposal", cases[0]["source"])   # inputs are not mutated


class AccuracyByDisposalTests(unittest.TestCase):
    def test_groups_and_rates(self):
        from scripts import accuracy_by_disposal as a

        self.assertEqual(a.group_of("ex_parte_decreed"), "ex parte decree")
        self.assertEqual(a.group_of("dismissed"), "contested (decreed or dismissed)")
        self.assertEqual(a.group_of("compromise"), "ended without a merits decision")
        self.assertEqual(a.group_of(None), "untagged")
        rows = [
            {"group": "g", "claimant_prevailed_ai": True, "claimant_prevailed_real": True},
            {"group": "g", "claimant_prevailed_ai": True, "claimant_prevailed_real": False},
            {"group": "g", "claimant_prevailed_ai": False, "claimant_prevailed_real": False},
            {"group": "g", "claimant_prevailed_ai": True, "claimant_prevailed_real": True},
            {"group": "h", "claimant_prevailed_ai": True, "claimant_prevailed_real": True},
        ]
        s = a.summarise_groups(rows)
        self.assertEqual(s["g"]["n"], 4)
        self.assertAlmostEqual(s["g"]["claimant_win_rate"], 0.5)
        self.assertAlmostEqual(s["g"]["pipeline_accuracy"], 0.75)
        self.assertAlmostEqual(s["g"]["respondent_win_recall"], 0.5)   # 1 of the 2 respondent wins predicted
        self.assertIsNone(s["h"]["respondent_win_recall"])
