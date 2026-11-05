"""Durable job queue: exclusive claims, retries, lease recovery after a crash, and the jobs.py integration."""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, update  # noqa: E402

from app import db, jobqueue, jobs  # noqa: E402


K = "unit-test-kind"  # a kind no live worker handles, so background workers started by other tests cannot steal these rows


def _clear() -> None:
    db.init_db()
    with db.engine.begin() as conn:
        conn.execute(delete(db.job_queue))


def _row(job_id: int) -> dict:
    with db.engine.connect() as conn:
        return dict(conn.execute(select(db.job_queue).where(db.job_queue.c.id == job_id)).mappings().first())


class QueueTests(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_fifo_and_empty(self):
        a = jobqueue.enqueue(K, {"case_id": "A"})
        b = jobqueue.enqueue(K, {"case_id": "B"})
        self.assertEqual(jobqueue.claim("w", kinds=(K,))["id"], a)
        self.assertEqual(jobqueue.claim("w", kinds=(K,))["id"], b)
        self.assertIsNone(jobqueue.claim("w", kinds=(K,)))

    def test_claim_is_exclusive_across_threads(self):
        ids = [jobqueue.enqueue(K, {"document_id": f"D{i}"}) for i in range(30)]
        got, lock = [], threading.Lock()

        def work(name):
            while (j := jobqueue.claim(name, kinds=(K,))) is not None:
                with lock:
                    got.append(j["id"])
                jobqueue.complete(j["id"])

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(got), sorted(ids))              # every job once, none twice
        self.assertEqual(len(got), len(set(got)))

    def test_failure_retries_with_backoff_then_gives_up(self):
        jid = jobqueue.enqueue(K, {"case_id": "X"}, max_attempts=2)
        jobqueue.claim("w", kinds=(K,))
        self.assertEqual(jobqueue.fail(jid, "boom"), "retry")
        self.assertIsNone(jobqueue.claim("w", kinds=(K,)))                   # backoff: not due yet
        with db.engine.begin() as conn:
            conn.execute(update(db.job_queue).where(db.job_queue.c.id == jid).values(run_after=0.0))
        self.assertEqual(jobqueue.claim("w", kinds=(K,))["attempts"], 2)
        self.assertEqual(jobqueue.fail(jid, "boom again"), "failed")
        self.assertEqual(_row(jid)["status"], "failed")
        self.assertIn("boom again", _row(jid)["last_error"])

    def test_expired_lease_is_reclaimed_but_a_live_lease_is_not(self):
        jid = jobqueue.enqueue(K, {"case_id": "L"})
        jobqueue.claim("dead-worker", lease_seconds=900, kinds=(K,))
        self.assertIsNone(jobqueue.claim("other", kinds=(K,)))               # still leased
        with db.engine.begin() as conn:
            conn.execute(update(db.job_queue).where(db.job_queue.c.id == jid).values(locked_until=time.time() - 1))
        again = jobqueue.claim("other", kinds=(K,))
        self.assertEqual((again["id"], again["attempts"]), (jid, 2))

    def test_lease_expiring_after_the_last_attempt_fails_the_job(self):
        jid = jobqueue.enqueue(K, {"case_id": "M"}, max_attempts=1)
        jobqueue.claim("w", kinds=(K,))
        with db.engine.begin() as conn:
            conn.execute(update(db.job_queue).where(db.job_queue.c.id == jid).values(locked_until=time.time() - 1))
        self.assertIsNone(jobqueue.claim("w", kinds=(K,)))
        self.assertEqual(_row(jid)["status"], "failed")

    def test_heartbeat_extends_the_lease_and_stats(self):
        jid = jobqueue.enqueue(K, {"case_id": "H"})
        jobqueue.claim("w", lease_seconds=1, kinds=(K,))
        jobqueue.heartbeat(jid, lease_seconds=600)
        self.assertGreater(_row(jid)["locked_until"], time.time() + 500)
        self.assertEqual(jobqueue.stats()["running"], 1)
        jobqueue.complete(jid)
        self.assertEqual(jobqueue.stats()["done"], 1)


class JobsIntegrationTests(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_handle_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            jobs._handle("nope", {})

    def test_a_second_start_while_busy_is_rejected_then_allowed_after_completion(self):
        from unittest import mock

        with mock.patch.object(jobs, "HANDLED_KINDS", ()):   # keep the live workers from running it
            self.assertTrue(jobs.start_pipeline("DN-DUP"))
            self.assertFalse(jobs.start_pipeline("DN-DUP"))
            self.assertFalse(jobs.start_resolution("DN-DUP", True))      # one phase per case at a time
            self.assertEqual(jobs.is_running("DN-DUP"), "pipeline")
            job = jobqueue.claim("t", kinds=("pipeline",))
            jobqueue.complete(job["id"])
            self.assertIsNone(jobs.is_running("DN-DUP"))
            self.assertTrue(jobs.start_resolution("DN-DUP", True))
            self.assertEqual(jobs.is_running("DN-DUP"), "resolution")

    def test_enqueue_failure_propagates_and_leaves_nothing_marked_running(self):
        from unittest import mock

        with mock.patch.object(jobs.jobqueue, "enqueue", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                jobs.start_pipeline("DN-ENQ-FAIL")
        self.assertIsNone(jobs.is_running("DN-ENQ-FAIL"))

    def test_work_left_by_a_dead_process_is_picked_up(self):
        """Simulates a crash: a running row with an expired lease for a document that was never extracted."""
        from app.storage import get_storage
        from tests.test_documents_jobs import _native_pdf_bytes

        case_id, doc_id = "DN-RECOVER", "DN-RECOVER-DOC-0"
        db.save_case({"case_id": case_id, "owner_id": "u1", "claim_amount": 50000.0, "dispute_type": "consumer_dispute", "evidence": []})
        path = get_storage().save(case_id, "doc.pdf", _native_pdf_bytes("Agreement dated 01/02/2024 for Rs. 50,000, signed."))
        db.insert_document({"id": doc_id, "case_id": case_id, "original_filename": "doc.pdf", "storage_path": path,
                            "mime_type": "application/pdf", "extraction_status": "pending"})
        # inserted directly as "running" with an expired lease, so no live worker can claim it before the test is ready
        import json as _json

        with db.engine.begin() as conn:
            jid = int(conn.execute(db.job_queue.insert().values(
                kind="extraction", payload=_json.dumps({"document_id": doc_id}), status="running", attempts=1, max_attempts=3,
                run_after=0.0, locked_by="process-that-died", locked_until=time.time() - 1, created_at=time.time() - 100,
            )).inserted_primary_key[0])

        jobs.ensure_workers()
        jobs._wake.set()
        deadline = time.time() + 30
        while time.time() < deadline and db.get_document(doc_id)["extraction_status"] != "complete":
            time.sleep(0.2)
        self.assertEqual(db.get_document(doc_id)["extraction_status"], "complete")
        deadline = time.time() + 10
        while time.time() < deadline and _row(jid)["status"] != "done":
            time.sleep(0.1)
        self.assertEqual(_row(jid)["status"], "done")


if __name__ == "__main__":
    unittest.main()


class StatusBeforeAnnouncementTests(unittest.TestCase):
    """A client that sees the terminal event and reads the case must already find the final status (race found by the load test)."""

    def test_case_status_is_updated_before_the_terminal_event_is_published(self):
        from unittest import mock

        _clear()
        case_id = "DN-ORDER"
        db.save_case({
            "case_id": case_id, "owner_id": "u1", "dispute_type": "consumer_dispute", "claim_amount": 50000.0,
            "claimant": {"name": "Asha"}, "respondent": {"name": "Ravi"},
            "description": "Paid Rs. 50,000 for a phone that was never delivered; the seller refuses a refund.",
            "evidence": [{"filename": "receipt.pdf", "kind": "document"}], "status": "ready",
        })
        seen: dict[str, str] = {}
        real_publish = jobs.bus.publish

        def spy(cid, ev):
            if ev.get("type") in ("awaiting_decision", "escalated_terminal", "resolved"):
                seen[ev["type"]] = db.get_case(cid)["status"]
            return real_publish(cid, ev)

        with mock.patch.object(jobs.bus, "publish", side_effect=spy):
            jobs._run_pipeline(case_id)
        self.assertTrue(seen, "the pipeline should have published a terminal event")
        for kind, status_at_publish in seen.items():
            self.assertNotEqual(status_at_publish, "processing", f"{kind} was published while the case still said processing")
