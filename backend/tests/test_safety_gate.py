"""Unit tests for app.core.safety_gate.

Uses plain dict mocks for case_input/pipeline_output rather than real
CaseContext/agent-result Pydantic objects -- the gate is duck-typed
specifically so these stay simple. Run with:

    python -m unittest tests.test_safety_gate -v      (from backend/)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.safety_gate import (  # noqa: E402
    EscalationCondition,
    check_escalation,
)

BASE_CASE = {
    "case_id": "DN-TEST-0001",
    "dispute_type": "consumer_dispute",
    "description": "Paid Rs. 5,000 for a laptop bag that arrived damaged. Seller refuses a refund.",
    "claim_amount": 5000.0,
    "respondent_submission": None,
}


def _case(**overrides) -> dict:
    return {**BASE_CASE, **overrides}


GOOD_PRECEDENTS = [
    {"id": "P1", "relief_amount_ratio": 0.8},
    {"id": "P2", "relief_amount_ratio": 0.75},
    {"id": "P3", "relief_amount_ratio": 0.9},
]

BASE_PIPELINE_OUTPUT = {
    "case_id": BASE_CASE["case_id"],
    "composite_confidence": {"score": 0.85},
    "research": {"precedents": GOOD_PRECEDENTS},
    "analysis": {
        "contradictions": ["No direct factual contradictions detected."],
        "strength": {"claimant": "strong", "respondent": "weak"},
    },
}


def _pipeline_output(**overrides) -> dict:
    merged = {**BASE_PIPELINE_OUTPUT, **overrides}
    return merged


class TestCheckpointADoesNotTriggerOnCleanCase(unittest.TestCase):
    def test_clean_civil_case_passes_pre_filter(self):
        result = check_escalation(_case())
        self.assertIsNone(result)


class TestCondition1CriminalMatter(unittest.TestCase):
    def test_triggers_on_assault_language(self):
        case = _case(
            description="The respondent assaulted me physically when I asked for a refund and threatened to kill me."
        )
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.CRIMINAL_MATTER.value, result.triggered_conditions)
        self.assertEqual(result.checkpoint, "pre_filter")

    def test_triggers_on_fir_mention_in_respondent_statement(self):
        case = _case(respondent_submission={"statement": "The claimant already filed a police complaint against me."})
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.CRIMINAL_MATTER.value, result.triggered_conditions)

    def test_ordinary_bank_fraud_language_does_not_false_positive(self):
        # "fraudulent"/"unauthorized" are legitimate civil consumer-dispute
        # signals (see app.agents.nlp SIGNAL_LEXICON) and must not alone
        # trip the criminal-matter stub.
        case = _case(
            dispute_type="consumer_dispute",
            description="An unauthorized, fraudulent transaction was made from my account via UPI without my consent.",
        )
        result = check_escalation(case)
        self.assertIsNone(result)


class TestCondition5JurisdictionMismatch(unittest.TestCase):
    def test_triggers_on_unregistered_dispute_type(self):
        case = _case(dispute_type="employment_dispute")
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.JURISDICTION_MISMATCH.value, result.triggered_conditions)
        self.assertEqual(result.checkpoint, "pre_filter")

    def test_triggers_on_custody_keyword_even_in_registered_category(self):
        case = _case(
            dispute_type="contract_breach",
            description="We had a written agreement about child custody arrangements that my ex-partner breached.",
        )
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.JURISDICTION_MISMATCH.value, result.triggered_conditions)

    def test_triggers_on_injunction_keyword(self):
        case = _case(description="I am seeking an injunction to stop the respondent from selling the disputed goods.")
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.JURISDICTION_MISMATCH.value, result.triggered_conditions)


class TestCondition2LowConfidence(unittest.TestCase):
    def test_triggers_below_hard_floor(self):
        output = _pipeline_output(composite_confidence={"score": 0.25})
        result = check_escalation(_case(), output)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.LOW_CONFIDENCE.value, result.triggered_conditions)
        self.assertEqual(result.checkpoint, "post_check")

    def test_does_not_trigger_at_or_above_floor(self):
        output = _pipeline_output(composite_confidence={"score": 0.4})
        result = check_escalation(_case(), output)
        self.assertIsNone(result)

    def test_accepts_bare_float_score_shape(self):
        output = _pipeline_output(composite_confidence=0.1)
        result = check_escalation(_case(), output)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.LOW_CONFIDENCE.value, result.triggered_conditions)


class TestCondition3NoPrecedent(unittest.TestCase):
    def test_triggers_on_zero_precedents(self):
        output = _pipeline_output(research={"precedents": []})
        result = check_escalation(_case(), output)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.NO_PRECEDENT.value, result.triggered_conditions)

    def test_triggers_on_single_precedent_below_min_of_two(self):
        output = _pipeline_output(research={"precedents": [GOOD_PRECEDENTS[0]]})
        result = check_escalation(_case(), output)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.NO_PRECEDENT.value, result.triggered_conditions)

    def test_does_not_trigger_with_two_or_more(self):
        output = _pipeline_output(research={"precedents": GOOD_PRECEDENTS[:2]})
        result = check_escalation(_case(), output)
        self.assertIsNone(result)


class TestCondition4AgentDisagreement(unittest.TestCase):
    def test_does_not_trigger_on_ordinary_litigant_quantum_gap(self):
        # Regression guard: a claimant-vs-respondent counter-offer gap
        # (analysis.contradictions' only real use in app.agents.analysis) is
        # normal for ANY contested case and must NOT be treated as agent
        # disagreement -- an earlier version of this check did, and it wrongly
        # escalated 4/18 golden eval cases that were simply contested with a
        # counter-offer (see scripts/eval_cases.py band A/C/E regressions).
        output = _pipeline_output(
            analysis={
                "contradictions": ["Quantum gap: claimant seeks Rs. 50,000 but respondent offers only Rs. 2,000."],
                "strength": {"claimant": "moderate", "respondent": "moderate"},
            }
        )
        result = check_escalation(_case(), output)
        self.assertIsNone(result)

    def test_triggers_on_precedent_vs_analysis_outcome_mismatch(self):
        # Precedents strongly favor relief, but Analysis independently rates
        # the claimant's case as weak -- a directional disagreement.
        output = _pipeline_output(
            research={"precedents": GOOD_PRECEDENTS},  # mean ratio ~0.82, >= AGREEMENT_RATIO_HIGH
            analysis={
                "contradictions": ["No direct factual contradictions detected."],
                "strength": {"claimant": "weak", "respondent": "strong"},
            },
        )
        result = check_escalation(_case(), output)
        self.assertIsNotNone(result)
        self.assertIn(EscalationCondition.AGENT_DISAGREEMENT.value, result.triggered_conditions)

    def test_does_not_trigger_when_aligned(self):
        result = check_escalation(_case(), _pipeline_output())
        self.assertIsNone(result)


class TestEscalationResultShape(unittest.TestCase):
    def test_result_contains_required_fields(self):
        result = check_escalation(_case(description="I was assaulted by the respondent."))
        self.assertIsNotNone(result)
        d = result.to_dict()
        self.assertIn("triggered_conditions", d)
        self.assertIn("details", d)
        self.assertIn("user_message", d)
        self.assertIn("court_registration_status", d)
        self.assertEqual(d["court_registration_status"], {
            "status": "not_integrated",
            "message": "Court database integration pending",
        })
        self.assertIn("case_id", d)
        self.assertIn("timestamp", d)
        self.assertTrue(d["user_message"])

    def test_multiple_conditions_can_trigger_together(self):
        case = _case(dispute_type="employment_dispute", description="The respondent assaulted me.")
        result = check_escalation(case)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.triggered_conditions), 2)
        self.assertIn(EscalationCondition.CRIMINAL_MATTER.value, result.triggered_conditions)
        self.assertIn(EscalationCondition.JURISDICTION_MISMATCH.value, result.triggered_conditions)


if __name__ == "__main__":
    unittest.main()
