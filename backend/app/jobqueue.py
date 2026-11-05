"""Durable job queue on the application database (no Redis or SQS needed).

Why: background work used to live only in a thread pool, so a restart or crash silently lost every queued or running
pipeline, resolution, extraction or discrepancy job. Each job is now a row in `job_queue`; workers claim rows, and a row whose
lease expires (a worker died) becomes claimable again. Delivery is AT LEAST ONCE, so handlers must be safe to repeat (the
existing jobs are idempotent per case and phase).

Portable between SQLite (development) and PostgreSQL (production): a claim is "pick a candidate, then UPDATE ... WHERE it is
still claimable"; exactly one worker's UPDATE changes a row, because the row's status or lease changes under it.
Later, workers can move to their own processes or machines without changing this contract.
"""
from __future__ import annotations

import json
import time

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from . import db

LEASE_SECONDS = 900.0          # a job that has not finished in this long is presumed lost and may be re-claimed
RETRY_BACKOFF_SECONDS = 5.0    # attempt n waits n * this before the next try


def _now() -> float:
    return time.time()


def enqueue(kind: str, payload: dict, max_attempts: int = 3, subject: str | None = None, dedupe_key: str | None = None) -> int | None:
    """Add a job. With a `dedupe_key`, returns None instead of adding a second job while one with the same key is queued or
    running (atomic, also across processes, because the key is UNIQUE while the job is active)."""
    try:
        with db.engine.begin() as conn:
            res = conn.execute(db.job_queue.insert().values(
                kind=kind, payload=json.dumps(payload), subject=subject, dedupe_key=dedupe_key, status="queued", attempts=0,
                max_attempts=max_attempts, run_after=0.0, created_at=_now()))
            return int(res.inserted_primary_key[0])
    except IntegrityError:
        if dedupe_key is None:
            raise
        return None


def active_kind(subject: str, kinds: tuple[str, ...]) -> str | None:
    """The kind of a queued or running job about `subject`, or None. This is how any process answers "is this case busy?"."""
    t = db.job_queue
    with db.engine.connect() as conn:
        row = conn.execute(select(t.c.kind).where(and_(t.c.subject == subject, t.c.kind.in_(kinds), t.c.status.in_(("queued", "running"))))
                           .order_by(t.c.id).limit(1)).first()
    return row[0] if row else None


def claim(worker_id: str, lease_seconds: float = LEASE_SECONDS, kinds: tuple[str, ...] | None = None) -> dict | None:
    """Claim the oldest runnable job, or None. Runnable = queued and due, or running with an expired lease.
    `kinds` restricts the claim to those job kinds, so worker pools can specialise (OCR workers, pipeline workers, ...)."""
    t = db.job_queue
    now = _now()
    claimable = or_(and_(t.c.status == "queued", t.c.run_after <= now),
                    and_(t.c.status == "running", t.c.locked_until < now))
    if kinds is not None:
        claimable = and_(claimable, t.c.kind.in_(kinds))
    for _ in range(5):  # another worker may win a candidate between our SELECT and UPDATE; try the next one
        with db.engine.begin() as conn:
            row = conn.execute(select(t).where(claimable).order_by(t.c.id).limit(1)).mappings().first()
            if row is None:
                return None
            if row["attempts"] >= row["max_attempts"] and row["status"] == "running":
                # lease expired after the last allowed attempt: give up rather than loop forever
                conn.execute(update(t).where(and_(t.c.id == row["id"], t.c.status == "running", t.c.locked_until < now)).values(
                    status="failed", finished_at=now, dedupe_key=None, last_error="lease expired after the last attempt"))
                continue
            res = conn.execute(update(t).where(and_(t.c.id == row["id"], claimable)).values(
                status="running", locked_by=worker_id, locked_until=now + lease_seconds, attempts=t.c.attempts + 1))
            if res.rowcount == 1:
                return {"id": row["id"], "kind": row["kind"], "payload": json.loads(row["payload"]), "attempts": row["attempts"] + 1,
                        "max_attempts": row["max_attempts"]}
    return None


def complete(job_id: int) -> None:
    with db.engine.begin() as conn:
        conn.execute(update(db.job_queue).where(db.job_queue.c.id == job_id).values(
            status="done", finished_at=_now(), locked_by=None, locked_until=None, dedupe_key=None))


def fail(job_id: int, error: str) -> str:
    """Record a failure. Returns 'retry' (back in the queue after a backoff) or 'failed' (attempts exhausted)."""
    t = db.job_queue
    with db.engine.begin() as conn:
        row = conn.execute(select(t.c.attempts, t.c.max_attempts).where(t.c.id == job_id)).first()
        if row is None:
            return "failed"
        if row.attempts < row.max_attempts:
            conn.execute(update(t).where(t.c.id == job_id).values(
                status="queued", run_after=_now() + RETRY_BACKOFF_SECONDS * row.attempts, locked_by=None,
                locked_until=None, last_error=error[:2000]))
            return "retry"
        conn.execute(update(t).where(t.c.id == job_id).values(
            status="failed", finished_at=_now(), locked_by=None, locked_until=None, dedupe_key=None, last_error=error[:2000]))
        return "failed"


def heartbeat(job_id: int, lease_seconds: float = LEASE_SECONDS) -> None:
    """Extend a running job's lease (for jobs that legitimately run longer than the lease)."""
    with db.engine.begin() as conn:
        conn.execute(update(db.job_queue).where(and_(db.job_queue.c.id == job_id, db.job_queue.c.status == "running")).values(
            locked_until=_now() + lease_seconds))


def stats() -> dict:
    """Counts by status, for health checks and dashboards."""
    from sqlalchemy import func

    t = db.job_queue
    with db.engine.connect() as conn:
        rows = conn.execute(select(t.c.status, func.count()).group_by(t.c.status)).all()
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    out.update({status: n for status, n in rows})
    return out
