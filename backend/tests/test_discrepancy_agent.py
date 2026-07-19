"""Unit tests for app.agents.discrepancy -- run against an in-memory sqlite
DB (DIGINYAYA_DB=":memory:") with DIGINYAYA_USE_LLM=0, so these test the
scripted-only detection path deterministically, same convention as
tests/test_safety_gate_integration.py.

Run with (from backend/):
    python -m unittest tests.test_discrepancy_agent -v
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
from app.agents import discrepancy  # noqa: E402
from app.core import safety_gate  # noqa: E402


def _make_case(case_id: str, claim_amount: float = 50000.0) -> None:
    db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": claim_amount, "dispute_type": "consumer_dispute"})


def _make_document(doc_id: str, case_id: str, cleaned_text: str, *, is_scanned: bool = False, ocr_confidence: float | None = None) -> str:
    # The test suite shares one process-wide in-memory sqlite connection
    # (app.db's module-level singleton), so a bare "DOC-1" id would collide
    # across test methods even though case_id differs -- id is the primary
    # key, not (case_id, id). Scoping the id to case_id keeps each test's
    # rows isolated. Returns the actual id used, since callers assert
    # against it.
    full_id = f"{case_id}-{doc_id}"
    db.insert_document({
        "id": full_id, "case_id": case_id, "original_filename": f"{doc_id}.pdf",
        "extraction_status": "complete", "is_scanned": is_scanned,
        "ocr_confidence": ocr_confidence, "cleaned_text": cleaned_text,
        "raw_ocr_text": cleaned_text,
    })
    return full_id


class TestDiscrepancyAgent(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_no_complete_documents_yields_done_with_zero_found(self):
        case_id = "DN-DISC-EMPTY"
        _make_case(case_id)
        events = list(discrepancy.run_discrepancy_check(case_id))
        self.assertEqual(events[-1]["type"], "discrepancy_check_done")
        self.assertEqual(events[-1]["payload"]["found"], 0)
        self.assertEqual(db.list_discrepancies(case_id), [])

    def test_blank_document_flagged_as_missing_element(self):
        case_id = "DN-DISC-BLANK"
        _make_case(case_id)
        doc_id = _make_document("DOC-1", case_id, "", is_scanned=True, ocr_confidence=0.5)

        events = list(discrepancy.run_discrepancy_check(case_id))
        found_events = [e for e in events if e["type"] == "discrepancy_found"]
        self.assertEqual(len(found_events), 1)
        self.assertEqual(found_events[0]["payload"]["discrepancy_type"], "missing_element")

        rows = db.list_discrepancies(case_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["document_ids"], [doc_id])

    def test_agreement_without_signature_flagged(self):
        case_id = "DN-DISC-NOSIG"
        _make_case(case_id)
        _make_document(
            "DOC-1", case_id,
            "This agreement is entered on 01/02/2024 between Alice and Bob for Rs. 50,000.",
        )
        list(discrepancy.run_discrepancy_check(case_id))
        rows = db.list_discrepancies(case_id)
        self.assertTrue(any("signature" in r["explanation"].lower() for r in rows))

    def test_signed_agreement_with_date_produces_no_discrepancy(self):
        case_id = "DN-DISC-CLEAN"
        _make_case(case_id)
        _make_document(
            "DOC-1", case_id,
            "This agreement is entered on 01/02/2024 between Alice and Bob for Rs. 50,000, "
            "signed by both parties in the presence of a witness.",
        )
        list(discrepancy.run_discrepancy_check(case_id))
        self.assertEqual(db.list_discrepancies(case_id), [])

    def test_no_date_in_document_flagged_low_severity(self):
        case_id = "DN-DISC-NODATE"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "Alice and Bob entered into an agreement for Rs. 50,000, duly signed.")
        list(discrepancy.run_discrepancy_check(case_id))
        rows = db.list_discrepancies(case_id)
        self.assertTrue(any(r["discrepancy_type"] == "missing_element" and r["severity"] == "low" for r in rows))

    def test_low_confidence_discrepancy_flagged_for_review_via_real_safety_gate_floor(self):
        # A very low OCR-confidence blank document should push
        # confidence_score below safety_gate.CONFIDENCE_FLOOR -- asserting
        # against the REAL imported constant (not a hardcoded 0.4) so this
        # test breaks if the two ever diverge.
        case_id = "DN-DISC-LOWCONF"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", is_scanned=True, ocr_confidence=0.05)
        list(discrepancy.run_discrepancy_check(case_id))
        rows = db.list_discrepancies(case_id)
        self.assertEqual(len(rows), 1)
        self.assertLess(rows[0]["confidence_score"], safety_gate.CONFIDENCE_FLOOR)
        self.assertTrue(rows[0]["flagged_for_review"])

    def test_high_confidence_native_document_discrepancy_not_flagged(self):
        case_id = "DN-DISC-HIGHCONF"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", is_scanned=False)  # native text, no OCR uncertainty
        list(discrepancy.run_discrepancy_check(case_id))
        rows = db.list_discrepancies(case_id)
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["confidence_score"], safety_gate.CONFIDENCE_FLOOR)
        self.assertFalse(rows[0]["flagged_for_review"])


class TestNameSimilarityDowngrade(unittest.TestCase):
    def test_similar_names_downgraded_to_low_with_ocr_note(self):
        candidate = {
            "discrepancy_type": "name_inconsistency",
            "document_ids": ["DOC-1", "DOC-2"],
            "explanation": "Name differs across documents.",
            "compared_values": ["Ramesh Kumar", "Ramesh Kurnar"],  # plausible OCR misread of 'm'->'rn'
            "severity": "medium",
        }
        result = discrepancy._name_similarity_downgrade(dict(candidate))
        self.assertEqual(result["severity"], "low")
        self.assertIn("OCR variation", result["explanation"])

    def test_dissimilar_names_not_downgraded(self):
        candidate = {
            "discrepancy_type": "name_inconsistency",
            "document_ids": ["DOC-1", "DOC-2"],
            "explanation": "Name differs across documents.",
            "compared_values": ["Ramesh Kumar", "Suresh Yadav"],
            "severity": "medium",
        }
        result = discrepancy._name_similarity_downgrade(dict(candidate))
        self.assertEqual(result["severity"], "medium")


if __name__ == "__main__":
    unittest.main()
