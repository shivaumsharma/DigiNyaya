"""Event bus across processes and a real worker process: the pieces that let the API and the workers run separately."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app import db, jobqueue, jobs  # noqa: E402
from app.core import events  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


def _reset() -> None:
    db.init_db()
    with db.engine.begin() as conn:
        conn.execute(delete(db.job_queue))
        conn.execute(delete(db.event_stream))


class DedupeAndStatusTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_dedupe_key_is_atomic_across_threads(self):
        results, lock = [], threading.Lock()

        def go():
            r = jobqueue.enqueue("unit-test-kind", {}, subject="S", dedupe_key="unit:S")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_key_is_released_when_the_job_finishes_or_fails_for_good(self):
        a = jobqueue.enqueue("unit-test-kind", {}, max_attempts=1, subject="S", dedupe_key="unit:S")
        self.assertIsNone(jobqueue.enqueue("unit-test-kind", {}, subject="S", dedupe_key="unit:S"))
        job = jobqueue.claim("w", kinds=("unit-test-kind",))
        self.assertEqual(jobqueue.fail(job["id"], "boom"), "failed")
        self.assertIsNotNone(jobqueue.enqueue("unit-test-kind", {}, subject="S", dedupe_key="unit:S"))
        self.assertNotEqual(a, None)

    def test_key_is_kept_while_a_failed_job_waits_for_its_retry(self):
        jobqueue.enqueue("unit-test-kind", {}, max_attempts=3, subject="S", dedupe_key="unit:S")
        job = jobqueue.claim("w", kinds=("unit-test-kind",))
        self.assertEqual(jobqueue.fail(job["id"], "transient"), "retry")
        self.assertIsNone(jobqueue.enqueue("unit-test-kind", {}, subject="S", dedupe_key="unit:S"))

    def test_active_kind_reflects_queue_state(self):
        self.assertIsNone(jobqueue.active_kind("X", ("unit-test-kind",)))
        jobqueue.enqueue("unit-test-kind", {}, subject="X")
        self.assertEqual(jobqueue.active_kind("X", ("unit-test-kind",)), "unit-test-kind")
        self.assertIsNone(jobqueue.active_kind("X", ("other",)))
        job = jobqueue.claim("w", kinds=("unit-test-kind",))
        jobqueue.complete(job["id"])
        self.assertIsNone(jobqueue.active_kind("X", ("unit-test-kind",)))


class DbEventBusTests(unittest.TestCase):
    def setUp(self):
        _reset()
        self.bus = events.DbEventBus()

    def test_subscriber_sees_only_events_published_after_subscribing_and_only_its_case(self):
        self.bus.publish("C1", {"type": "old"})
        sub = self.bus.subscribe("C1")
        self.bus.publish("C2", {"type": "other-case"})
        self.bus.publish("C1", {"type": "a", "seq": 1})
        self.bus.publish("C1", {"type": "b", "seq": 2})
        self.assertEqual([sub.get(timeout=1.0)["type"], sub.get(timeout=1.0)["type"]], ["a", "b"])
        with self.assertRaises(queue.Empty):
            sub.get(timeout=0.2)

    def test_publish_from_another_thread_reaches_a_waiting_subscriber(self):
        sub = self.bus.subscribe("C3")
        threading.Timer(0.3, lambda: self.bus.publish("C3", {"type": "late"})).start()
        t0 = time.time()
        self.assertEqual(sub.get(timeout=5.0)["type"], "late")
        self.assertLess(time.time() - t0, 2.0)

    def test_stream_from_queue_works_with_a_db_subscription_and_stops_at_terminal(self):
        sub = self.bus.subscribe("C4")
        for t in ("token", "token", "resolved"):
            self.bus.publish("C4", {"type": t})
        got = [e["type"] for e in events.stream_from_queue(sub, idle_timeout=5)]
        self.assertEqual(got, ["token", "token", "resolved"])

    def test_old_rows_are_pruned(self):
        bus = events.DbEventBus()
        bus.PRUNE_EVERY = 1
        with db.engine.begin() as conn:
            conn.execute(db.event_stream.insert().values(case_id="OLD", payload="{}", created_at=time.time() - 10_000))
        bus.publish("NEW", {"type": "x"})
        with db.engine.connect() as conn:
            left = conn.execute(select(db.event_stream.c.case_id)).scalars().all()
        self.assertEqual(left, ["NEW"])

    def test_corrupt_rows_are_skipped(self):
        sub = self.bus.subscribe("C5")
        with db.engine.begin() as conn:
            conn.execute(db.event_stream.insert().values(case_id="C5", payload="not json", created_at=time.time()))
        self.bus.publish("C5", {"type": "ok"})
        self.assertEqual(sub.get(timeout=1.0)["type"], "ok")

    def test_bus_selection_by_env(self):
        with mock.patch.dict(os.environ, {"DIGINYAYA_EVENT_BUS": "memory"}):
            self.assertIsInstance(events._make_bus(), events.EventBus)
        with mock.patch.dict(os.environ, {"DIGINYAYA_EVENT_BUS": "db"}):
            self.assertIsInstance(events._make_bus(), events.DbEventBus)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def _bus_with_history_marked(self):
        bus = events.DbEventBus()
        bus.subscribe("warmup")          # starts the dispatcher and marks existing rows as history
        time.sleep(0.2)
        return bus

    def test_a_late_committed_lower_id_is_still_delivered_exactly_once(self):
        bus = self._bus_with_history_marked()
        q = bus.subscribe("OO")
        with db.engine.begin() as conn:
            conn.execute(db.event_stream.insert().values(id=10_000, case_id="OO", payload='{"type":"second"}', created_at=time.time()))
        self.assertEqual(q.get(timeout=2.0)["type"], "second")
        with db.engine.begin() as conn:     # an earlier id that only became visible later (concurrent commit order)
            conn.execute(db.event_stream.insert().values(id=9_999, case_id="OO", payload='{"type":"first"}', created_at=time.time()))
        self.assertEqual(q.get(timeout=2.0)["type"], "first")
        time.sleep(0.5)
        self.assertTrue(q.empty(), "an event must not be delivered twice")

    def test_many_streams_cost_one_query_per_poll(self):
        from sqlalchemy import event

        bus = events.DbEventBus()
        qs = [bus.subscribe(f"case-{i % 20}") for i in range(200)]
        time.sleep(0.3)
        bus.publish("case-3", {"type": "x"})
        selects = []

        def count(conn, cursor, statement, *a):
            if statement.lstrip().upper().startswith("SELECT") and "event_stream" in statement:
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            n = bus.poll_once()
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        self.assertEqual(len(selects), 1)
        self.assertEqual(n, 1)
        got = sum(1 for q in qs if not q.empty())
        self.assertEqual(got, 10)            # the 10 subscribers of case-3 (200 / 20), nobody else

    def test_dispatcher_stops_when_nobody_listens_and_restarts(self):
        bus = events.DbEventBus()
        q = bus.subscribe("R")
        bus.unsubscribe("R", q)
        deadline = time.time() + 3
        while time.time() < deadline and bus._thread is not None:
            time.sleep(0.05)
        self.assertIsNone(bus._thread)
        q2 = bus.subscribe("R")
        time.sleep(0.2)
        bus.publish("R", {"type": "again"})
        self.assertEqual(q2.get(timeout=3.0)["type"], "again")


class AsyncStreamTests(unittest.TestCase):
    def test_astream_yields_events_heartbeats_and_stops_at_terminal(self):
        import asyncio

        async def run():
            q: queue.Queue = queue.Queue()
            q.put({"type": "token"})

            async def later():
                await asyncio.sleep(0.35)
                q.put({"type": "resolved"})

            asyncio.get_event_loop().create_task(later())
            return [e["type"] async for e in events.astream(q, idle_timeout=10, heartbeat_every=0.1)]

        got = asyncio.run(run())
        self.assertEqual(got[0], "token")
        self.assertEqual(got[-1], "resolved")
        self.assertIn("heartbeat", got)

    def test_astream_gives_up_when_idle(self):
        import asyncio

        async def run():
            return [e async for e in events.astream(queue.Queue(), idle_timeout=0.3, heartbeat_every=5)]

        self.assertEqual(asyncio.run(run()), [])


class RealWorkerProcessTests(unittest.TestCase):
    """Start `python -m app.worker` as a separate process, enqueue from this one, and watch the live events arrive."""

    def setUp(self):
        _reset()

    def test_a_separate_worker_process_runs_a_pipeline_and_its_events_stream_back(self):
        case_id = "DN-WORKER-PROC"
        db.save_case({
            "case_id": case_id, "owner_id": "u1", "dispute_type": "consumer_dispute", "claim_amount": 50000.0,
            "claimant": {"name": "Asha"}, "respondent": {"name": "Ravi"}, "description": "Paid Rs. 50,000 for a phone that was never delivered; seller refuses a refund.",
            "evidence": [{"filename": "receipt.pdf", "kind": "document"}], "status": "ready",
        })
        # Use the engine's real database, not the environment variable: other test modules overwrite DIGINYAYA_DB after import.
        if db.engine.dialect.name == "sqlite" and db.engine.url.database in (None, "", ":memory:"):
            self.skipTest("the test database is in memory, so another process cannot share it")
        env = {**os.environ, "DIGINYAYA_DB": db.engine.url.render_as_string(hide_password=False),
               "DIGINYAYA_USE_LLM": "0", "DIGINYAYA_LLM_PROVIDER": "mock",
               "DIGINYAYA_JOB_WORKERS": "1", "PYTHONPATH": str(BACKEND)}
        with mock.patch.object(jobs, "HANDLED_KINDS", ()):          # this process must not run the job itself
            sub = events.bus.subscribe(case_id) if isinstance(events.bus, events.DbEventBus) else events.DbEventBus().subscribe(case_id)
            self.assertTrue(jobs.start_pipeline(case_id))
            self.assertEqual(jobs.is_running(case_id), "pipeline")   # visible from this process, run by another
            proc = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=BACKEND, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            seen: list[str] = []
            try:
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        ev = sub.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    seen.append(ev.get("type"))
                    if ev.get("type") in events.TERMINAL:
                        break
                deadline = time.time() + 30
                while time.time() < deadline and jobs.is_running(case_id):
                    time.sleep(0.2)
            finally:
                proc.terminate()
                try:
                    out, _ = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
        self.assertTrue(any(t in events.TERMINAL for t in seen), f"no terminal event; saw {seen[:10]}; worker output:\n{out[-1500:]}")
        self.assertIsNone(jobs.is_running(case_id))
        self.assertTrue(db.get_events(case_id), "the worker should have written the durable event log")
        self.assertEqual(proc.returncode in (0, -15), True)


class RequestSessionTests(unittest.TestCase):
    def test_release_closes_the_session_and_returns_its_connection(self):
        from app.auth.db import SessionLocal, engine
        from app.auth.orm_models import User
        from app.main import _release_request_session

        sess = SessionLocal()
        try:
            user = User(email="release-test@example.com", password_hash="x", full_name="R", preferred_language="en-IN")
            sess.add(user)
            sess.commit()
            sess.query(User).filter(User.id == user.id).first()       # opens a transaction: holds a connection
            before = engine.pool.checkedout()
            self.assertGreaterEqual(before, 1)
            uid = user.id
            _release_request_session(user)
            self.assertEqual(engine.pool.checkedout(), before - 1)
            self.assertIsNone(__import__("sqlalchemy.orm", fromlist=["object_session"]).object_session(user))
            sess2 = SessionLocal()
            sess2.query(User).filter(User.id == uid).delete()
            sess2.commit()
            sess2.close()
        finally:
            sess.close()


class WorkerSwitchTests(unittest.TestCase):
    def test_embedded_workers_can_be_disabled(self):
        with mock.patch.object(jobs, "_RUN_WORKERS", False), mock.patch.object(jobs, "_workers_started", False), \
                mock.patch.object(jobs, "_worker_threads", []) as threads:
            jobs.ensure_workers()
            self.assertEqual(threads, [])
            self.assertFalse(jobs._workers_started)


if __name__ == "__main__":
    unittest.main()
