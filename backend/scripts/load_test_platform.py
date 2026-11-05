"""User-journey load test for the whole platform (API + workers + database + live event stream).

Each virtual user runs one complete journey, timed step by step:
  signup -> browse (matters, dispute types) -> sample claim -> create case -> skip respondent -> run pipeline
  -> open the live event stream (SSE) and read it to the end -> read the final case

while a sampler polls /api/health for the job-queue depth. It reports per-step p50/p95/p99, errors, end-to-end time,
journeys per second and the peak queue depth. Run it against any deployment (single process, or API plus workers).

WHAT IT MEASURES: the platform's own capacity (HTTP handling, password hashing at signup, database, the job queue, workers,
the event stream). With no LLM key the pipeline uses the scripted engine, so there is NO LLM latency or quota in the
numbers: add those separately (see docs/LOAD_TEST.md). It also runs on one machine in the usual setup, so the load
generator competes with the system under test for CPU; treat results as a lower bound for a dedicated deployment.

Run (from backend/), after starting the API (and workers) with DIGINYAYA_ENV=development so plain http works:
    python -m scripts.load_test_platform --base-url http://127.0.0.1:8000 --users 50 --ramp 5
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import uuid
from collections import defaultdict

import requests

STEPS = ("signup", "browse", "sample_claim", "create_case", "skip_response", "run", "sse_first_event", "sse_to_end", "get_case", "journey")
TERMINAL_STATUSES = {"resolved", "escalated", "mediation_proposed", "awaiting_decision", "error"}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(pct / 100 * (len(s) - 1))))]


class Recorder:
    def __init__(self) -> None:
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.err: dict[str, int] = defaultdict(int)
        self.err_samples: dict[str, str] = {}
        self.lock = threading.Lock()

    def ok(self, step: str, seconds: float) -> None:
        with self.lock:
            self.lat[step].append(seconds)

    def fail(self, step: str, why: str) -> None:
        with self.lock:
            self.err[step] += 1
            self.err_samples.setdefault(step, why[:160])


def timed(rec: Recorder, step: str, fn):
    t0 = time.monotonic()
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001 - every failure mode is data here
        rec.fail(step, f"{type(exc).__name__}: {exc}")
        return None
    rec.ok(step, time.monotonic() - t0)
    return out


def journey(base: str, rec: Recorder, stream_timeout: float) -> None:
    t_start = time.monotonic()
    s = requests.Session()
    email = f"lt-{uuid.uuid4().hex[:12]}@example.com"

    def signup():
        r = s.post(f"{base}/auth/signup/email", json={"email": email, "password": "Loadtest-pass-1", "full_name": "Load Test",
                                                      "preferred_language": "en-IN"}, timeout=60)
        r.raise_for_status()
        return r.json()["access_token"]

    token = timed(rec, "signup", signup)
    if not token:
        return
    h = {"Authorization": f"Bearer {token}"}

    def browse():
        for path in ("/api/matters", "/api/dispute-types"):
            s.get(base + path, timeout=60).raise_for_status()

    timed(rec, "browse", browse)
    claim = timed(rec, "sample_claim", lambda: s.get(f"{base}/api/sample-claim", headers=h, timeout=60).json()["claim"])
    if not claim:
        return
    case_id = timed(rec, "create_case", lambda: _ok(s.post(f"{base}/api/cases", json=claim, headers=h, timeout=60)).json()["case_id"])
    if not case_id:
        return
    if timed(rec, "skip_response", lambda: _ok(s.post(f"{base}/api/cases/{case_id}/skip-response", headers=h, timeout=60))) is None:
        return
    if timed(rec, "run", lambda: _ok(s.post(f"{base}/api/cases/{case_id}/run", headers=h, timeout=60))) is None:
        return

    # Live stream: time to the first real event and to the end of the stream.
    t0 = time.monotonic()
    first = None
    try:
        with s.get(f"{base}/api/cases/{case_id}/events?after=0", headers=h, stream=True, timeout=(30, stream_timeout)) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if line.startswith("data:") and first is None:
                    first = time.monotonic() - t0
                    rec.ok("sse_first_event", first)
                if line.startswith("event: end"):
                    break
                if time.monotonic() - t0 > stream_timeout:
                    raise TimeoutError("stream did not end")
        rec.ok("sse_to_end", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        rec.fail("sse_to_end", f"{type(exc).__name__}: {exc}")
        return

    def final():
        c = s.get(f"{base}/api/cases/{case_id}", headers=h, timeout=60)
        c.raise_for_status()
        status = c.json().get("status")
        if status not in TERMINAL_STATUSES:
            raise RuntimeError(f"case not finished: {status}")
        return status

    if timed(rec, "get_case", final) is not None:
        rec.ok("journey", time.monotonic() - t_start)


def _ok(resp):
    resp.raise_for_status()
    return resp


def sample_queue(base: str, stop: threading.Event, out: list[dict]) -> None:
    while not stop.is_set():
        try:
            q = requests.get(f"{base}/api/health", timeout=10).json().get("queue") or {}
            out.append({"t": time.monotonic(), **q})
        except Exception:  # noqa: BLE001
            pass
        stop.wait(0.5)


def run(base: str, users: int, ramp: float, stream_timeout: float) -> dict:
    rec = Recorder()
    samples: list[dict] = []
    stop = threading.Event()
    sampler = threading.Thread(target=sample_queue, args=(base, stop, samples), daemon=True)
    sampler.start()
    t0 = time.monotonic()
    threads = []
    for _ in range(users):
        th = threading.Thread(target=journey, args=(base, rec, stream_timeout), daemon=True)
        th.start()
        threads.append(th)
        if ramp:
            time.sleep(random.uniform(0, 2 * ramp / max(users, 1)))
    for th in threads:
        th.join(stream_timeout + 120)
    wall = time.monotonic() - t0
    stop.set()
    done = len(rec.lat["journey"])
    peak_q = max((s.get("queued", 0) for s in samples), default=0)
    peak_r = max((s.get("running", 0) for s in samples), default=0)
    summary = {"users": users, "ramp_s": ramp, "wall_s": round(wall, 1), "journeys_ok": done, "journeys_per_s": round(done / wall, 2) if wall else 0,
               "peak_queued": peak_q, "peak_running": peak_r, "steps": {}}
    for step in STEPS:
        v = rec.lat.get(step, [])
        summary["steps"][step] = {"n": len(v), "errors": rec.err.get(step, 0), "p50": round(percentile(v, 50), 3),
                                  "p95": round(percentile(v, 95), 3), "p99": round(percentile(v, 99), 3), "max": round(max(v), 3) if v else 0}
    summary["error_samples"] = dict(rec.err_samples)
    return summary


def print_summary(s: dict) -> None:
    print(f"\nusers={s['users']} ramp={s['ramp_s']}s  wall={s['wall_s']}s  completed journeys={s['journeys_ok']}/{s['users']}  "
          f"({s['journeys_per_s']}/s)  peak queue: {s['peak_queued']} queued, {s['peak_running']} running")
    print(f"{'step':18s}{'ok':>6s}{'err':>6s}{'p50':>9s}{'p95':>9s}{'p99':>9s}{'max':>9s}")
    for step, v in s["steps"].items():
        print(f"{step:18s}{v['n']:>6d}{v['errors']:>6d}{v['p50']:>9.3f}{v['p95']:>9.3f}{v['p99']:>9.3f}{v['max']:>9.3f}")
    for step, why in s["error_samples"].items():
        print(f"  error sample [{step}]: {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--ramp", type=float, default=5.0, help="seconds over which users start (0 = all at once)")
    ap.add_argument("--stream-timeout", type=float, default=180.0)
    ap.add_argument("--json", default=None, help="also write the summary to this file")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    try:
        requests.get(f"{base}/api/health", timeout=10).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot reach {base}: {exc}")
        return 1
    s = run(base, args.users, args.ramp, args.stream_timeout)
    print_summary(s)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    return 0 if s["journeys_ok"] == args.users else 2


if __name__ == "__main__":
    sys.exit(main())
