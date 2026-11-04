"""Matter registry: the capability policy is enforced as code, the evaluated civil types are untouched, and the API serves guides."""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.loader import DISPUTE_TYPES, list_dispute_types  # noqa: E402
from app.matters import (  # noqa: E402
    GATE_CHECKS,
    RESTRICTED_FAMILIES,
    Level,
    Matter,
    get_matter,
    list_matters,
    to_public,
    validate_matter,
)
from app.matters import guides  # noqa: E402


class PolicyTests(unittest.TestCase):
    def test_every_registered_matter_satisfies_the_policy(self):
        problems = [p for m in list_matters() for p in validate_matter(m)]
        self.assertEqual(problems, [])

    def test_restricted_families_never_exceed_l1(self):
        for m in list_matters():
            if m.family in RESTRICTED_FAMILIES:
                self.assertLessEqual(m.level, Level.L1_PREPARE, m.id)

    def test_validate_catches_violations(self):
        bad = Matter(id="x", label="X", family="criminal", level=Level.L2_RESOLVE, description="d", pipeline_type="money_recovery")
        self.assertTrue(any("may not exceed" in p for p in validate_matter(bad)))
        no_guide = Matter(id="y", label="Y", family="family", level=Level.L0_NAVIGATE, description="d")
        self.assertTrue(any("need a guide" in p for p in validate_matter(no_guide)))
        self.assertTrue(any("unknown gate check" in p for p in validate_matter(
            Matter(id="z", label="Z", family="civil", level=Level.L2_RESOLVE, description="d", pipeline_type="money_recovery", gate_passed=("nope",)))))

    def test_only_consumer_is_autonomous(self):
        self.assertEqual({m.id for m in list_matters() if m.level == Level.L3_DECIDE}, {"consumer_dispute"})

    def test_only_pipeline_types_can_resolve(self):
        ids = {dt["id"] for dt in DISPUTE_TYPES}
        for m in list_matters():
            if m.can_resolve():
                self.assertIn(m.id, ids)
            if m.status == "navigator":
                self.assertFalse(m.can_resolve())

    def test_unvalidated_judge_and_lawyer_review_are_visible_as_missing(self):
        missing = get_matter("money_recovery").missing_gate_checks()
        self.assertIn("judge_validated_vs_humans", missing)
        self.assertIn("lawyer_reviewed", missing)
        self.assertEqual(set(get_matter("money_recovery").gate_passed) - set(GATE_CHECKS), set())


class OverlayDoesNotChangeTheCivilTypes(unittest.TestCase):
    def test_dispute_type_list_is_unchanged_by_the_registry(self):
        self.assertEqual([d["id"] for d in list_dispute_types()],
                         ["consumer_dispute", "money_recovery", "contract_breach", "cheque_bounce",
                          "tenancy_dispute", "property_dispute", "employment_dispute"])
        self.assertNotIn("criminal_complaint", {d["id"] for d in DISPUTE_TYPES})

    def test_preview_types_stay_preview_until_enabled(self):
        self.assertEqual(get_matter("tenancy_dispute").status, "preview")
        self.assertEqual(get_matter("consumer_dispute").status, "live")


class GuideContentTests(unittest.TestCase):
    def test_guides_are_conservative(self):
        for g in guides.GUIDES:
            text = " ".join([g["summary"], g["forum"], *g["steps"]]).lower()
            for banned in ("you will win", "likely to win", "guilty", "limitation period of", "years from", "₹"):
                self.assertNotIn(banned, text, f"{g['id']}: {banned}")
            self.assertIsNone(re.search(r"\brs\.? ?\d", text), f"{g['id']}: states an amount")
            self.assertTrue(g["steps"] and g["forum"])

    def test_every_guide_carries_the_disclaimer_and_is_unreviewed(self):
        for m in list_matters():
            if m.guide:
                self.assertFalse(m.guide["reviewed"])
                self.assertIn("not legal advice", m.guide["disclaimer"].lower())
                self.assertTrue(m.guide["this_tool_cannot"])

    def test_ids_are_unique(self):
        ids = [m.id for m in list_matters()]
        self.assertEqual(len(ids), len(set(ids)))


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def test_list_is_public_and_exposes_level_and_gate(self):
        r = self.client.get("/api/matters")
        self.assertEqual(r.status_code, 200)
        by_id = {m["id"]: m for m in r.json()}
        self.assertEqual(by_id["consumer_dispute"]["level_name"], "L3_DECIDE")
        self.assertEqual(by_id["criminal_complaint"]["level"], 0)
        self.assertFalse(by_id["criminal_complaint"]["can_resolve"])
        self.assertIn("lawyer_reviewed", by_id["money_recovery"]["gate_missing"])
        self.assertNotIn("guide", by_id["criminal_complaint"])

    def test_guide_endpoint(self):
        r = self.client.get("/api/matters/criminal_complaint/guide")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["reviewed"])
        self.assertTrue(body["steps"])
        self.assertIn("disclaimer", body)

    def test_unknown_and_pipeline_matters_have_no_guide(self):
        self.assertEqual(self.client.get("/api/matters/nope/guide").status_code, 404)
        self.assertEqual(self.client.get("/api/matters/money_recovery/guide").status_code, 404)

    def test_public_shape(self):
        pub = to_public(get_matter("cheque_bounce"))
        self.assertTrue(pub["can_resolve"])
        self.assertEqual(pub["family"], "civil")


if __name__ == "__main__":
    unittest.main()
