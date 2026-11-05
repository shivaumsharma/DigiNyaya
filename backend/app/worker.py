"""Run the background job workers in their own process.

    python -m app.worker

Use this when the API should not do the heavy work itself (LLM calls, OCR, the agent pipeline): start one or more worker
processes (on the same machine or others) against the same database, and start the API with DIGINYAYA_RUN_WORKERS=0.
Workers claim jobs from the `job_queue` table, publish live events through the shared event bus (DIGINYAYA_EVENT_BUS=db,
the default), and stop cleanly on SIGTERM, finishing in-flight jobs first. A worker that dies mid-job loses nothing: another
worker picks the job up once its lease expires.

Environment: DIGINYAYA_DB (same value as the API), DIGINYAYA_JOB_WORKERS (threads per process), plus the LLM settings the
API uses.
"""
from __future__ import annotations

import logging

from . import db, jobs


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_db()
    jobs.run_worker_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
