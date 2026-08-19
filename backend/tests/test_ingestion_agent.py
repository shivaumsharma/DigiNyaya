"""Unit tests for app.agents.ingestion -- in particular the evidence
RELEVANCE (not just count) fix added after a real test case (2026-08-05):
"amazon scammed me of my 10000" with two attached PDFs that were the
claimant's own school marksheets -- completely unrelated to the dispute --
still scored 95% ingestion confidence and routed to fully autonomous Tier 1.

document_relevance (an LLM call, shared with app.agents.preliminary_review)
is mocked directly rather than going through llm.generate_json, matching
this suite's convention of patching at the call site (see
tests/test_mediation_agent.py).

Run with (from backend/):
    python -m unittest tests.test_ingestion_agent -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DIGINYAYA_DB"] = ":memory:"
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.agents import ingestion  # noqa: E402
from app.core.context import CaseContext  # noqa: E402


def _ctx(case_id: str, *, description: str, evidence: list[dict], respondent_statement: str | None = None) -> CaseContext:
    return CaseContext(
        case_id=case_id, owner_id="u1", dispute_type="consumer_dispute",
        claimant_name="Claimant", respondent_name="Respondent", claim_amount=40000.0,
        description=description, evidence=evidence,
        respondent_submission={"statement": respondent_statement} if respondent_statement else None,
    )


def _make_doc(doc_id: str, case_id: str, *, extraction_status: str = "complete") -> None:
    full_id = f"{case_id}-{doc_id}"
    db.insert_document({
        "id": full_id, "case_id": case_id, "original_filename": f"{doc_id}.pdf",
        "extraction_status": extraction_status, "cleaned_text": "irrelevant" if extraction_status == "complete" else "",
        "raw_ocr_text": "",
    })


def _relevance(relevant: bool, looks_like: str) -> dict:
    return {
        "document_id": "x", "filename": "x.pdf", "relevant": relevant,
        "looks_like": looks_like, "note": "", "authenticity_flag": False, "authenticity_note": "",
    }


class TestIngestionEvidenceRelevance(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_irrelevant_evidence_does_not_boost_confidence_or_eligibility(self):
        case_id = "DN-ING-IRRELEVANT"
        _make_doc("D1", case_id)
        _make_doc("D2", case_id)
        ctx = _ctx(
            case_id,
            description="amazon scammed me of my 10000",
            evidence=[{"filename": "10th_marksheet.pdf", "kind": "document"}, {"filename": "12th_marksheet.pdf", "kind": "document"}],
        )
        with patch("app.agents.ingestion.document_relevance", side_effect=[
            _relevance(False, "a school marksheet"), _relevance(False, "a school marksheet"),
        ]):
            result = ingestion.run(ctx)
        self.assertFalse(result.output.tier1_eligible)
        self.assertEqual(result.output.recommended_tier, 2)
        self.assertIn("does not appear to relate", result.output.reasoning)

    def test_relevant_evidence_is_eligible_for_tier_1(self):
        case_id = "DN-ING-RELEVANT"
        _make_doc("D1", case_id)
        ctx = _ctx(
            case_id,
            description="Amazon sold me a defective bluetooth speaker for Rs 4599 and refused a refund.",
            evidence=[{"filename": "order.pdf", "kind": "document"}],
        )
        with patch("app.agents.ingestion.document_relevance", return_value=_relevance(True, "an Amazon order confirmation")):
            result = ingestion.run(ctx)
        self.assertTrue(result.output.tier1_eligible)
        self.assertEqual(result.output.recommended_tier, 1)

    def test_no_evidence_at_all_stays_ineligible(self):
        # Pre-existing behavior, must survive the rewrite: no documents to
        # check at all -> no relevant evidence -> not eligible.
        case_id = "DN-ING-NONE"
        ctx = _ctx(case_id, description="Amazon scammed me.", evidence=[])
        result = ingestion.run(ctx)
        self.assertFalse(result.output.tier1_eligible)

    def test_mixed_relevant_and_irrelevant_counts_the_relevant_one(self):
        case_id = "DN-ING-MIXED"
        _make_doc("D1", case_id)
        _make_doc("D2", case_id)
        ctx = _ctx(
            case_id,
            description="Amazon sold me a defective bluetooth speaker for Rs 4599 and refused a refund.",
            evidence=[{"filename": "order.pdf", "kind": "document"}, {"filename": "marksheet.pdf", "kind": "document"}],
        )
        with patch("app.agents.ingestion.document_relevance", side_effect=[
            _relevance(True, "an Amazon order confirmation"), _relevance(False, "a school marksheet"),
        ]):
            result = ingestion.run(ctx)
        self.assertTrue(result.output.tier1_eligible)

    def test_pending_extraction_not_counted_either_way(self):
        case_id = "DN-ING-PENDING"
        _make_doc("D1", case_id, extraction_status="pending")
        ctx = _ctx(case_id, description="Amazon scammed me.", evidence=[{"filename": "receipt.pdf", "kind": "document"}])
        result = ingestion.run(ctx)
        # Not complete yet -> document_relevance is never even called for it.
        self.assertFalse(result.output.tier1_eligible)

    def test_hostile_respondent_reply_does_not_falsely_trigger_service_deficiency(self):
        # Regression test for the exact reported bug: "support" as a bare
        # keyword matched inside a hostile, non-substantive respondent reply
        # ("why do you support such application"), misclassifying the case
        # and inflating confidence via a signal that had nothing to do with
        # customer support.
        case_id = "DN-ING-HOSTILE"
        ctx = _ctx(
            case_id, description="amazon scammed me of my 10000", evidence=[],
            respondent_statement="the fuck is this why do you support such application we reject claim no proof",
        )
        result = ingestion.run(ctx)
        self.assertNotEqual(result.output.dispute_subtype, "Deficiency of service")


if __name__ == "__main__":
    unittest.main()
