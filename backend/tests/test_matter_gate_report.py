"""The gate report counts real cases and the frozen test set honestly, never marks manual checks as met, and flags an
inconsistent registry. Uses synthetic records only."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matters import GATE_CHECKS, get_matter  # noqa: E402
from scripts.matter_gate_report import MANUAL_STEPS, MEASURED, assess, categories_for, count_cases, count_test  # noqa: E402

LIMITS = {"min_cases": 10, "min_contested": 4, "min_test": 2}


def _case(i, disposal, cat="tenancy_disputes"):
    return {"case_id": f"DC-EVAL-{i}", "category": cat, "source": {"disposal": disposal}}


class Categories(unittest.TestCase):
    def test_native_and_original_types_map_to_distinct_categories(self):
        self.assertEqual(categories_for("tenancy_dispute"), ["tenancy_disputes"])
        self.assertEqual(categories_for("cheque_bounce"), ["cheque_bounce"])
        # tenancy cases are not double counted under the type they are scored as
        self.assertNotIn("tenancy_disputes", categories_for("money_recovery"))
        self.assertFalse(any(c.startswith("escalation__") for t in ("money_recovery", "consumer_dispute") for c in categories_for(t)))


class Counting(unittest.TestCase):
    def test_counts_endings_and_contested_and_ignores_other_categories(self):
        cases = [_case(1, "decreed"), _case(2, "dismissed"), _case(3, "ex_parte_decreed"), _case(4, None), _case(5, "unknown"),
                 _case(6, "decreed", cat="cheque_bounce")]
        c = count_cases(cases, ["tenancy_disputes"])
        self.assertEqual((c["total"], c["with_ending"], c["contested"]), (5, 3, 2))
        self.assertEqual(c["by_disposal"]["untagged"], 1)

    def test_test_set_count(self):
        m = {"by_category": {"tenancy_disputes": {"dev": 9, "test": 4}, "cheque_bounce": {"dev": 1, "test": 1}}}
        self.assertEqual(count_test(m, ["tenancy_disputes"]), 4)
        self.assertIsNone(count_test(None, ["tenancy_disputes"]))


class Assess(unittest.TestCase):
    def rows(self, counts, test_n, passed=()):
        return {r["check"]: r for r in assess("tenancy_dispute", counts, test_n, passed, LIMITS)}

    def test_every_gate_check_is_reported_once(self):
        r = self.rows({"total": 0, "by_disposal": {}, "with_ending": 0, "contested": 0}, None)
        self.assertEqual(set(r), set(GATE_CHECKS))
        self.assertEqual(set(MANUAL_STEPS) | set(MEASURED), set(GATE_CHECKS))

    def test_thresholds(self):
        ok = {"total": 20, "by_disposal": {}, "with_ending": 12, "contested": 5}
        self.assertEqual(self.rows(ok, 2)["real_cases_with_endings"]["status"], "MET")
        self.assertEqual(self.rows(ok, 2)["frozen_contested_test_set"]["status"], "MET")
        many_but_uncontested = {**ok, "contested": 3}
        self.assertEqual(self.rows(many_but_uncontested, 2)["real_cases_with_endings"]["status"], "NOT_MET")
        self.assertEqual(self.rows(ok, 1)["frozen_contested_test_set"]["status"], "NOT_MET")

    def test_missing_data_is_unknown_not_failed_or_passed(self):
        r = self.rows({"total": 0, "by_disposal": {}, "with_ending": 0, "contested": 0}, None)
        self.assertEqual(r["real_cases_with_endings"]["status"], "UNKNOWN")
        self.assertEqual(r["frozen_contested_test_set"]["status"], "UNKNOWN")

    def test_manual_checks_are_never_marked_met(self):
        good = {"total": 99, "by_disposal": {}, "with_ending": 99, "contested": 99}
        r = self.rows(good, 99)
        for c in MANUAL_STEPS:
            self.assertEqual(r[c]["status"], "MANUAL")

    def test_registry_claim_without_data_is_flagged(self):
        none = {"total": 0, "by_disposal": {}, "with_ending": 0, "contested": 0}
        r = self.rows(none, None, passed=("real_cases_with_endings", "lawyer_reviewed"))
        self.assertTrue(r["real_cases_with_endings"]["inconsistent"])
        self.assertFalse(r["lawyer_reviewed"]["inconsistent"])  # manual: the script cannot contradict it


class TenancyModule(unittest.TestCase):
    def test_tenancy_has_a_guide_and_no_gate_checks_passed(self):
        m = get_matter("tenancy_dispute")
        self.assertIsNotNone(m.guide)
        self.assertEqual(m.gate_passed, ())
        self.assertFalse(m.guide["reviewed"])

    def test_tenancy_guide_warns_against_self_help_and_says_state_law_varies(self):
        g = get_matter("tenancy_dispute").guide
        self.assertIn("lock out", " ".join(g["steps"]).lower())
        self.assertIn("state", g["forum"].lower())


class CompanionGuides(unittest.TestCase):
    """Rules every companion guide must keep until a lawyer has reviewed it."""

    def test_every_companion_guide_attaches_to_a_pipeline_type_and_stays_unreviewed(self):
        from app.matters import guides

        for tid in guides.COMPANION_GUIDES:
            m = get_matter(tid)
            self.assertIsNotNone(m, tid)
            self.assertIsNotNone(m.pipeline_type, tid)
            self.assertIsNotNone(m.guide, tid)
            self.assertFalse(m.guide["reviewed"], tid)
            self.assertEqual(len(m.guide["documents"]) >= 4, True, tid)

    def test_no_legal_numbers_or_named_statutes(self):
        import re

        from app.matters import guides

        for tid in guides.COMPANION_GUIDES:
            g = get_matter(tid).guide
            text = " ".join([g["summary"], g["forum"], *g["steps"], *g["documents"]]).lower()
            self.assertIsNone(re.search(r"\b\d+\s*(days?|months?|years?)\b", text), tid)
            self.assertIsNone(re.search(r"(rs\.?|₹)\s*\d", text), tid)
            self.assertIsNone(re.search(r"\bsection\s+\d|\bact,?\s+\d{4}|\bcode\b", text), tid)

    def test_each_guide_sends_the_reader_to_confirm_forum_and_time_limit(self):
        from app.matters import guides

        for tid in guides.COMPANION_GUIDES:
            g = get_matter(tid).guide
            self.assertTrue(any("time limit" in x.lower() for x in g["steps"]), tid)
            self.assertTrue(any(k in g["forum"].lower() for k in ("lawyer", "legal services")), tid)

    def test_property_guide_says_urgent_injunctions_go_to_a_human_and_warns_against_self_help(self):
        g = get_matter("property_dispute").guide
        self.assertIn("injunction", g["forum"].lower())
        self.assertIn("human", g["forum"].lower())
        self.assertTrue(any("do not" in x.lower() and "enter" in x.lower() for x in g["steps"]))

    def test_property_guide_matches_what_the_safety_gate_really_does(self):
        from app.core.safety_gate import OUT_OF_SCOPE_INDICATORS

        self.assertTrue(any("injunction" in k for k in OUT_OF_SCOPE_INDICATORS))

    def test_partnership_guide_points_to_the_deed_and_forbids_moving_firm_money(self):
        g = get_matter("partnership_dispute").guide
        self.assertIn("deed", g["forum"].lower())
        self.assertTrue(any("do not" in x.lower() and "firm" in x.lower() for x in g["steps"]))

    def test_employment_guide_does_not_claim_one_forum(self):
        g = get_matter("employment_dispute").guide
        self.assertIn("depends", g["forum"].lower())
        self.assertIn("labour and industrial", g["forum"].lower())


class TenancyApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def test_guide_is_served_while_the_filing_type_stays_on_the_roadmap(self):
        listed = {m["id"]: m for m in self.client.get("/api/matters").json()}["tenancy_dispute"]
        self.assertTrue(listed["has_guide"])
        self.assertEqual(listed["status"], "preview")
        g = self.client.get("/api/matters/tenancy_dispute/guide")
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()["language"], "en")
        self.assertFalse(g.json()["reviewed"])
        # other pipeline types still have no guide
        self.assertEqual(self.client.get("/api/matters/cheque_bounce/guide").status_code, 404)

    def test_untranslated_guide_falls_back_to_english(self):
        g = self.client.get("/api/matters/tenancy_dispute/guide?lang=hi-IN").json()
        self.assertEqual(g["language"], "en")


class Cli(unittest.TestCase):
    def test_report_runs_end_to_end_on_a_synthetic_corpus(self):
        import json
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            cases = [_case(i, "decreed" if i % 2 else "dismissed") for i in range(12)]
            cp = Path(d) / "cases.json"
            cp.write_text(json.dumps(cases))
            man = Path(d) / "m.json"
            man.write_text(json.dumps({"by_category": {"tenancy_disputes": {"dev": 8, "test": 4}}}))
            out = subprocess.run([sys.executable, "-m", "scripts.matter_gate_report", "--matter", "tenancy_dispute", "--cases", str(cp),
                                  "--manifest", str(man), "--no-tag", "--min-cases", "10", "--min-contested", "10", "--min-test", "3"],
                                 cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("MET      real_cases_with_endings", out.stdout)
        self.assertIn("MET      frozen_contested_test_set", out.stdout)
        self.assertIn("Open checks (4/6)", out.stdout)


if __name__ == "__main__":
    unittest.main()
