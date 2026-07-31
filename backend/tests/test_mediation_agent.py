"""Unit tests for app.agents.mediation -- in particular the deterministic
net-strength enforcement added after a real-judgment-style test case (0
evidence claimant, non-substantive respondent reply) produced a nonzero
settlement despite the respondent being independently scored stronger.

DIGINYAYA_USE_LLM=0 makes the scripted default path already net_strength-safe
(proposed_ratio is 0 whenever net_strength <= 0), so the bug this guards
against only manifests when the LLM's returned relief_ratio violates its own
prompt instruction -- these tests mock app.agents.mediation.llm.generate_json
directly to simulate exactly that violation.

Run with (from backend/):
    python -m unittest tests.test_mediation_agent -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import mediation  # noqa: E402
from app.core.context import AnalysisResult, CaseContext, IngestionResult, ResearchResult, RetrievedPrecedent  # noqa: E402


def _precedent(id_="P1", ratio=0.8, days=30) -> RetrievedPrecedent:
    return RetrievedPrecedent(
        id=id_, title="Test Precedent", court="Test Forum", year=2024, citation="Test v. Precedent (2024)",
        summary="s", principle="p", outcome="o", outcome_detail="od",
        relief_amount_ratio=ratio, compliance_days=days, relevance=1.0,
    )


def _ctx(*, c_strength: float, r_strength: float, claim_amount: float = 50000.0) -> CaseContext:
    ctx = CaseContext(
        case_id="DN-MED-TEST", owner_id="u1", dispute_type="consumer_dispute",
        claimant_name="Claimant", respondent_name="Respondent", claim_amount=claim_amount,
        description="Test claim.", respondent_submission={"statement": "test", "accepts_liability": False},
    )
    ctx.ingestion = IngestionResult(
        dispute_subtype="General", claim_amount=claim_amount, claim_amount_display="Rs. 50,000",
        evidence_count=0, respondent_responded=True, tier1_eligible=False, recommended_tier=2, confidence=0.5,
        relief_type_requested="monetary",
    )
    ctx.research = ResearchResult(
        precedents=[_precedent()], corpus_size=100, coverage_score=0.8, coverage_label="strong", method="keyword",
    )
    ctx.analysis = AnalysisResult(strength_score={"claimant": c_strength, "respondent": r_strength})
    return ctx


class TestMediationNetStrengthEnforcement(unittest.TestCase):
    def test_scripted_default_already_dismisses_when_respondent_at_least_as_strong(self):
        # No LLM mock -- this exercises the pre-existing scripted formula,
        # which was already net_strength-safe. Confirms the baseline hasn't
        # regressed, not the new fix itself.
        ctx = _ctx(c_strength=0.2, r_strength=0.45)
        result = mediation.run(ctx)
        self.assertEqual(result.output.type, "dismissed")
        self.assertEqual(result.output.amount, 0.0)

    def test_llm_violating_its_own_instruction_is_overridden_to_dismissed(self):
        # Simulates exactly the reproduced bug: analysis scores the
        # respondent stronger (45% vs 20%), but the LLM's JSON response
        # proposes partial relief anyway, contradicting the prompt's own
        # explicit instruction to dismiss in this situation.
        ctx = _ctx(c_strength=0.2, r_strength=0.45)
        fake_llm_response = {
            "outcome_type": "partial_refund",
            "relief_ratio": 0.6,
            "compliance_days": 30,
            "rationale": "Respondent's stronger case warrants partial relief.",
        }
        with patch("app.agents.mediation.llm.generate_json", return_value=fake_llm_response):
            result = mediation.run(ctx)
        self.assertEqual(result.output.type, "dismissed")
        self.assertEqual(result.output.amount, 0.0)
        self.assertIn("forced from", " ".join(result.output.validator_notes).lower())
        # The stale LLM rationale (written for the overridden 60% relief)
        # must not survive into the final explanation -- it would otherwise
        # contradict the dismissed outcome.
        self.assertNotIn("warrants partial relief", result.output.explanation)

    def test_llm_relief_kept_when_claimant_genuinely_stronger(self):
        # Guards against the enforcement being overzealous -- a claimant
        # who IS stronger than the respondent should still get the LLM's
        # proposed relief, unmodified by this new check.
        ctx = _ctx(c_strength=0.8, r_strength=0.2)
        fake_llm_response = {
            "outcome_type": "partial_refund",
            "relief_ratio": 0.6,
            "compliance_days": 30,
            "rationale": "Claimant's well-evidenced case supports partial relief.",
        }
        with patch("app.agents.mediation.llm.generate_json", return_value=fake_llm_response):
            result = mediation.run(ctx)
        self.assertEqual(result.output.type, "partial_refund")
        self.assertAlmostEqual(result.output.amount, 30000.0)

    def test_equal_strength_also_dismisses(self):
        # "at least as strong" per the prompt's own wording -- a tie must
        # not favor the claimant.
        ctx = _ctx(c_strength=0.5, r_strength=0.5)
        fake_llm_response = {"outcome_type": "compensation", "relief_ratio": 0.4, "compliance_days": 30, "rationale": "x"}
        with patch("app.agents.mediation.llm.generate_json", return_value=fake_llm_response):
            result = mediation.run(ctx)
        self.assertEqual(result.output.type, "dismissed")
        self.assertEqual(result.output.amount, 0.0)


if __name__ == "__main__":
    unittest.main()
