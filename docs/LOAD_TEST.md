# Load test: what the platform can take today (5 Nov 2026)

Tool: `backend/scripts/load_test_platform.py`. Each virtual user runs a full journey (sign up, browse, create a case, run the
pipeline, read the live event stream to the end, read the final case). Reported per step with p50/p95/p99, plus journeys per
second and the peak job-queue depth. `DIGINYAYA_SIMULATED_JOB_SECONDS` (default 0, load testing only) makes each pipeline job
wait that long, to emulate time spent on a real LLM.

## Read these limits first
* One 4-core, 16 GB machine ran the load generator, the API, the workers and PostgreSQL together, so every number is a lower
  bound for a dedicated deployment and competes with itself for CPU.
* **No LLM was involved.** The pipeline ran on the scripted engine, so the numbers cover the platform (HTTP, auth, database,
  queue, workers, event stream) and NOT LLM latency, LLM quota or cost per case. Those need your Sarvam key.
* Bursts of 50 to 300 users started within 5 to 10 seconds, one journey each. Not a sustained soak test; nothing ran for hours.
* Plain HTTP on localhost: no TLS, no CDN, no real network latency.

## Results (scripted engine)
| Setup | Users (burst) | Completed | Journey p50 / p95 | Notes |
|---|---|---|---|---|
| A: SQLite, 1 API process, 4 embedded worker threads | 50 | 50 of 50 | 0.4 s / 0.4 s | |
| A | 150 | 150 of 150 | 9.3 s / 11.2 s | |
| A | 300 | 300 of 300 | 27.9 s / 29.8 s | signup p50 7.6 s (bcrypt CPU) |
| B: PostgreSQL, 2 API processes, 2 worker processes x 4 threads | 150 | 150 of 150 | 2.8 s / 5.1 s | 10.9 journeys/s |
| B | 300 | 300 of 300 | 18.5 s / 23.9 s | 10.2 journeys/s, zero errors |

Before the fixes below, A failed at 150 users (2 stale-status errors) and collapsed at 300 (about 150 HTTP 500s, 30 s waits).

### LLM-bound work (each pipeline waits 10 s, 60 users)
| Setup | Worker threads | Wall time | Result |
|---|---|---|---|
| A | 4 | 125 s | the live stream hit its 120 s idle limit for queued cases (12 clients saw "not finished") |
| B | 16 (2 processes x 8) | 42.6 s | 60 of 60 completed |
The time matches the arithmetic: jobs / threads x seconds per job (60 / 4 x 10 = 150 s; 60 / 16 x 10 = 40 s).

## Problems the load test found, now fixed
1. **Stale status after the stream ended.** The terminal event was published before the case status was updated, so a client
   that read the case right after saw "processing". Status is now updated first (regression test included).
2. **Event stream design.** Each live stream used a worker thread (limit about 40 streams, starving every other request) and my
   first database-backed bus polled per stream. Now: an async stream endpoint plus ONE dispatcher thread per process (one query
   per poll however many streams are open), and the stream releases its auth database session before it starts.
3. **Connection pool exhaustion.** SQLAlchemy's default pool (5 + 10) caused HTTP 500s from 150 users. Pools are now sized
   (`DIGINYAYA_DB_POOL_SIZE`, `DIGINYAYA_DB_MAX_OVERFLOW`; SQLite default overflow 250, PostgreSQL 40 per engine, so keep
   (size + overflow) x engines x processes below the database's `max_connections`). Signup now hashes the password before
   its first query so it does not hold a connection through the CPU work.
4. **Several API processes need a shared signing key.** Without `DIGINYAYA_JWT_SECRET` each process generates its own, so a
   token from one process was rejected (401) by another. The app now logs a warning when it is unset.

## Re-run on 6 Nov: a fifth defect, and the limit
Same machine class (4 cores), PostgreSQL 16 with `max_connections` 600, 2 API processes (`uvicorn --workers 2`) and 2 worker processes.
The documented 300-user result did **not** reproduce here: both the code before and after the changes below completed only 121 to 212
of 300 journeys with HTTP 500s. `pg_stat_activity` showed the cause: 40 to 48 connections "idle in transaction" after the user
lookup. Every authenticated request kept its auth database session (and pooled connection) open until the response was sent, so
requests queued for a worker thread while each held a connection, and the pool (10 + 40) ran dry.

Fix: `current_user_detached` (app/auth/deps.py) closes that session right after the lookup; the case and document endpoints use it
(the user row stays readable; code that modifies the user keeps `current_user`). Login also no longer holds a connection during
bcrypt (and always does one bcrypt comparison, so response time does not reveal whether an email exists). Regression tests cover both
(the pool test fails if the release is removed).

| Setup | Users | Completed | Journey p50 / p95 | Errors |
|---|---|---|---|---|
| B (as above), before the fix | 300 | 121 to 212 of 300 | 15 to 77 s | HTTP 500 and timeouts |
| B, after the fix | 150 | 150 of 150 | 9.2 s / 11.7 s | 0 |
| B, after the fix | 300 | 300 of 300 | 17.6 s / 24.9 s | 0 (10.0 journeys/s) |
| B, after the fix | 500 | 169 of 500 | 121 s / 123 s | signup HTTP 500 (213) and timeouts |

So on this machine the ceiling is between 300 and 500 simultaneous sign-ups-plus-journeys. Sign-up is CPU bound (bcrypt) and the load
generator shares the 4 cores, so this is a lower bound; the 500-user failure was not investigated further. The numbers in the earlier
table above were measured in an earlier session on a different PostgreSQL configuration and are not directly comparable.

## Event bus: LISTEN/NOTIFY
On PostgreSQL the event bus now wakes on `NOTIFY` (sent in the same transaction as the insert) instead of polling every 50 to 250 ms;
polling remains as a 2-second safety net and as the only mode on SQLite. `tests/test_event_bus_postgres.py` (needs
`DIGINYAYA_TEST_PG_URL`) proves an event reaches the dispatcher in under 1.5 s with the poll interval set to 30 s, and fails if the
NOTIFY is removed. Not measured: the reduction in database queries under load.

## Known gaps
* A stream ends after 120 s without events; a queued case can wait longer. Clients must reconnect with `?after=<last seq>`
  (the Resolve page now reconnects automatically while the pipeline is running: exponential back-off, from the last event seen).
* On SQLite the event bus still polls; on PostgreSQL it uses LISTEN/NOTIFY with a 2 s polling safety net. For tens of thousands of
  concurrent streams across many hosts, Redis behind the same three methods would be the next step.
* Signup is CPU-bound by design (bcrypt, about 0.3 s each, roughly 13 per second on 4 cores): scale API processes for signup bursts.

## Capacity model (fill in with measured LLM numbers)
Worker threads needed = cases per second x seconds of LLM time per pipeline. Threads are cheap while waiting on an LLM, so the
binding limits are the provider's rate limits and your cost per case.

| Cases per hour | 30 s per pipeline | 60 s | 120 s |
|---|---|---|---|
| 1,000 | 8 threads | 17 | 33 |
| 10,000 | 83 | 167 | 333 |

Not yet measured (needs a Sarvam key): LLM calls per case, seconds per case, tokens and cost per case, provider rate limits.
Run `scripts/measure_eval_cost.py` and one `load_test_platform` run with the key set to fill these in.
