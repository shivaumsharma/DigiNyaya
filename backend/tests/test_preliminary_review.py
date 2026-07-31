"""Unit tests for app.agents.preliminary_review -- run against an in-memory
sqlite DB (DIGINYAYA_DB=":memory:") with DIGINYAYA_USE_LLM=0, so these test
the no-LLM fallback paths (no text extracted / LLM unavailable) deterministically,
same convention as tests/test_discrepancy_agent.py. The per-document LLM
relevance call itself (_document_relevance's happy path) is proven separately
by a live manual run against Sarvam, not re-mocked here.

Run with (from backend/):
    python -m unittest tests.test_preliminary_review -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ["DIGINYAYA_DB"] = ":memory:"
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.agents import preliminary_review  # noqa: E402


def _make_case(case_id: str, dispute_type: str = "money_recovery", description: str = "") -> None:
    db.save_case({
        "case_id": case_id, "owner_id": "u1", "claim_amount": 50000.0,
        "dispute_type": dispute_type, "description": description,
    })


def _make_document(doc_id: str, case_id: str, cleaned_text: str, *, extraction_status: str = "complete") -> str:
    full_id = f"{case_id}-{doc_id}"
    db.insert_document({
        "id": full_id, "case_id": case_id, "original_filename": f"{doc_id}.pdf",
        "extraction_status": extraction_status, "cleaned_text": cleaned_text,
        "raw_ocr_text": cleaned_text,
    })
    return full_id


class TestPreliminaryReview(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_no_documents_recommends_expected_evidence_for_dispute_type(self):
        case_id = "DN-PRELIM-EMPTY"
        _make_case(case_id, dispute_type="money_recovery")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(result["documents"], [])
        self.assertIn("bank transfer record", result["case_strength_note"])

    def test_document_with_no_extracted_text_marked_uncertain_not_irrelevant(self):
        case_id = "DN-PRELIM-BLANK"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(len(result["documents"]), 1)
        self.assertIsNone(result["documents"][0]["relevant"])
        self.assertIn("No readable text", result["documents"][0]["note"])
        # Authenticity is a *separate* claim from relevance -- no text means
        # no assessment either way, not a fabrication accusation.
        self.assertIsNone(result["documents"][0]["authenticity_flag"])

    def test_llm_unavailable_authenticity_also_defaults_to_uncertain(self):
        case_id = "DN-PRELIM-NOLLM-AUTH"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "Some document text with actual content in it.")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIsNone(result["documents"][0]["authenticity_flag"])
        self.assertEqual(result["documents"][0]["authenticity_note"], "")

    def test_llm_unavailable_falls_back_to_uncertain_not_false_accusation(self):
        # DIGINYAYA_USE_LLM=0 makes llm.generate_json return falsy -- this
        # is the "couldn't assess" path, which must stay None (not False),
        # since a wrongly-flagged "irrelevant" would be a false accusation.
        case_id = "DN-PRELIM-NOLLM"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "Some document text with actual content in it.")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(len(result["documents"]), 1)
        self.assertIsNone(result["documents"][0]["relevant"])
        self.assertIn("Couldn't assess", result["documents"][0]["note"])

    def test_pending_extraction_reported_separately_from_relevance(self):
        case_id = "DN-PRELIM-PENDING"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", extraction_status="pending")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(result["documents"], [])
        self.assertIn("still being processed", result["case_strength_note"])

    def test_unknown_dispute_type_falls_back_to_generic_expected_evidence(self):
        case_id = "DN-PRELIM-GENERIC"
        _make_case(case_id, dispute_type="not_a_real_type")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIn("documentary proof supporting the claim", result["case_strength_note"])

    def test_vague_description_flagged_as_not_detailed_enough(self):
        case_id = "DN-PRELIM-VAGUE"
        _make_case(case_id, description="he robbed me of my money i dont have any proof but i know it")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertFalse(result["description_review"]["detailed_enough"])
        self.assertTrue(result["description_review"]["note"])

    def test_specific_description_with_amount_and_dates_passes(self):
        case_id = "DN-PRELIM-SPECIFIC"
        _make_case(
            case_id,
            description=(
                "I lent Rs 4,00,000 to the respondent via bank transfer on 10 Jan 2024 as an "
                "interest-free personal loan, repayable within 6 months. He has repaid nothing "
                "and stopped responding to messages since March 2024."
            ),
        )
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertTrue(result["description_review"]["detailed_enough"])

    def test_winnability_score_present_and_low_for_empty_case(self):
        case_id = "DN-PRELIM-WINEMPTY"
        _make_case(case_id, description="he robbed me, no proof but i know it")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIn("score", result["winnability"])
        self.assertIn("label", result["winnability"])
        self.assertLessEqual(result["winnability"]["score"], 40)
        self.assertEqual(result["winnability"]["label"], "weak")

    def test_winnability_score_higher_with_relevant_evidence_and_detail(self):
        case_id = "DN-PRELIM-WINSTRONG"
        _make_case(
            case_id,
            description=(
                "I lent Rs 4,00,000 to the respondent via bank transfer on 10 Jan 2024 as an "
                "interest-free personal loan, repayable within 6 months. He has repaid nothing."
            ),
        )
        _make_document("DOC-1", case_id, "Bank transfer receipt for Rs 4,00,000 dated 10-Jan-2024.")
        # DIGINYAYA_USE_LLM=0 for this module -> document relevance is
        # "uncertain" (None), not "relevant" (True), since that check also
        # needs the LLM -- so the scripted winnability fallback here can only
        # credit the longer description, not the (unassessed) document.
        result = preliminary_review.run_preliminary_review(case_id)
        empty_case_id = "DN-PRELIM-WINEMPTY2"
        _make_case(empty_case_id, description="he robbed me, no proof but i know it")
        empty_result = preliminary_review.run_preliminary_review(empty_case_id)
        self.assertGreater(result["winnability"]["score"], empty_result["winnability"]["score"])


if __name__ == "__main__":
    unittest.main()
