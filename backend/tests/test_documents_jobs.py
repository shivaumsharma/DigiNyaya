"""Integration tests for the document-extraction / discrepancy-check job
wiring in app.jobs -- exercises the generator functions directly
(jobs.run_extraction, discrepancy.run_discrepancy_check) rather than through
the threaded jobs.start_* wrappers, mirroring how
tests/test_safety_gate_integration.py tests graph.run_pipeline directly
instead of jobs.start_pipeline.

Run with (from backend/):
    python -m unittest tests.test_documents_jobs -v
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

os.environ["DIGINYAYA_DB"] = ":memory:"
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402

from app import db, jobs  # noqa: E402
from app.storage import get_storage  # noqa: E402


def _native_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    raw = doc.tobytes()
    doc.close()
    return raw


class TestRunExtractionGenerator(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_extracts_native_pdf_and_yields_completion_event(self):
        case_id = "DN-JOBS-1"
        db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": 1000.0, "dispute_type": "consumer_dispute", "evidence": []})

        raw = _native_pdf_bytes("Invoice dated 01/02/2024 for Rs. 1,000.")
        storage_path = get_storage().save(case_id, "invoice.pdf", raw)
        doc_id = f"{case_id}-DOC-1"
        db.insert_document({
            "id": doc_id, "case_id": case_id, "original_filename": "invoice.pdf",
            "storage_path": storage_path, "mime_type": "application/pdf", "extraction_status": "pending",
        })

        events = list(jobs.run_extraction(doc_id))
        self.assertEqual(events[-1]["type"], "document_extracted")
        self.assertIn("Invoice dated 01/02/2024", events[-1]["payload"]["cleaned_text"])

    def test_missing_document_yields_error_event(self):
        events = list(jobs.run_extraction("DOC-does-not-exist"))
        self.assertEqual(events[0]["type"], "error")

    def test_unreadable_storage_path_yields_failure_event(self):
        case_id = "DN-JOBS-2"
        db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": 1000.0, "dispute_type": "consumer_dispute", "evidence": []})
        doc_id = f"{case_id}-DOC-1"
        db.insert_document({
            "id": doc_id, "case_id": case_id, "original_filename": "invoice.pdf",
            "storage_path": "nonexistent/path.pdf", "mime_type": "application/pdf", "extraction_status": "pending",
        })
        events = list(jobs.run_extraction(doc_id))
        self.assertEqual(events[-1]["type"], "document_extraction_failed")


class TestExtractionJobPersistsAndAutoTriggers(unittest.TestCase):
    """Exercises the full threaded path (start_extraction) since the
    persistence + auto-trigger logic lives in the side-effecting wrapper
    (_run_extraction), not the pure generator -- this is the one seam that
    genuinely needs the real threaded flow to verify end-to-end."""

    def setUp(self):
        db.init_db()

    def _wait_until_idle(self, case_id: str, doc_ids: list[str], timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (
                not any(jobs.is_document_extracting(d) for d in doc_ids)
                and not jobs.is_discrepancy_check_running(case_id)
            ):
                return
            time.sleep(0.05)
        self.fail("Extraction/discrepancy-check job(s) did not finish within timeout")

    def test_two_documents_auto_trigger_discrepancy_check_once_both_complete(self):
        case_id = "DN-JOBS-AUTO"
        db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": 50000.0, "dispute_type": "consumer_dispute", "evidence": []})

        storage = get_storage()
        doc_ids = []
        for i, text in enumerate([
            "Agreement dated 01/02/2024 for Rs. 50,000, signed by both parties.",
            "Receipt dated 02/02/2024 for Rs. 45,000, signed.",
        ]):
            raw = _native_pdf_bytes(text)
            storage_path = storage.save(case_id, f"doc{i}.pdf", raw)
            doc_id = f"{case_id}-DOC-{i}"
            db.insert_document({
                "id": doc_id, "case_id": case_id, "original_filename": f"doc{i}.pdf",
                "storage_path": storage_path, "mime_type": "application/pdf", "extraction_status": "pending",
            })
            doc_ids.append(doc_id)

        for doc_id in doc_ids:
            jobs.start_extraction(doc_id)
        self._wait_until_idle(case_id, doc_ids)

        for doc_id in doc_ids:
            self.assertEqual(db.get_document(doc_id)["extraction_status"], "complete")

        event_types = [e["type"] for e in db.get_events(case_id)]
        self.assertIn("discrepancy_check", event_types)
        self.assertIn("discrepancy_check_done", event_types)

    def test_single_document_case_does_not_auto_trigger(self):
        # Nothing to cross-check with only one document -- auto-trigger
        # should stay a no-op until a second document lands or the endpoint
        # is called explicitly.
        case_id = "DN-JOBS-SINGLE"
        db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": 50000.0, "dispute_type": "consumer_dispute", "evidence": []})

        raw = _native_pdf_bytes("Agreement dated 01/02/2024 for Rs. 50,000, signed.")
        storage_path = get_storage().save(case_id, "doc.pdf", raw)
        doc_id = f"{case_id}-DOC-0"
        db.insert_document({
            "id": doc_id, "case_id": case_id, "original_filename": "doc.pdf",
            "storage_path": storage_path, "mime_type": "application/pdf", "extraction_status": "pending",
        })

        jobs.start_extraction(doc_id)
        self._wait_until_idle(case_id, [doc_id])

        self.assertEqual(db.get_document(doc_id)["extraction_status"], "complete")
        event_types = [e["type"] for e in db.get_events(case_id)]
        self.assertNotIn("discrepancy_check", event_types)


if __name__ == "__main__":
    unittest.main()
