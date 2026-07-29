"""Concurrency/latency load test against a running DigiNyaya backend.

One-off/dev-time tool, not a runtime API. Two modes:

  light     Hammers cheap, unauthenticated read endpoints (ai-status,
            dispute-types, precedents) with N concurrent requests. Measures
            raw HTTP/server concurrency handling -- nothing LLM-related.

  pipeline  The real claim: provisions N distinct phone-auth accounts, then
            concurrently files a sample case on each, skips the respondent
            step, triggers the 5-agent pipeline, and polls until the case
            reaches a terminal status (resolved / escalated / error). Timed
            end-to-end, wall clock, exactly what a citizen would experience.
            This burns real Sarvam API calls -- N of them, not more, since
            concurrency is capped at N and each case runs once.

Usage (from the backend folder):
    python scripts/load_test.py light    --base-url https://diginyaya-backend.onrender.com --concurrency 10
    python scripts/load_test.py pipeline --base-url https://diginyaya-backend.onrender.com --concurrency 5
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TERMINAL_STATUSES = {"resolved", "escalated", "error", "mediation_proposed"}
LIGHT_ENDPOINTS = ["/api/ai-status", "/api/dispute-types", "/api/precedents"]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(pct / 100 * (len(s) - 1))))
    return s[idx]


def report(label: str, latencies: list[float], errors: int, total: int) -> None:
    print(f"\n=== {label} ===")
    print(f"requests: {total}  errors: {errors}  success_rate: {100 * (total - errors) / total:.1f}%")
    if latencies:
        print(f"p50: {percentile(latencies, 50):.3f}s")
        print(f"p95: {percentile(latencies, 95):.3f}s")
        print(f"p99: {percentile(latencies, 99):.3f}s")
        print(f"min: {min(latencies):.3f}s  max: {max(latencies):.3f}s")


# --------------------------------------------------------------------------- #
# light mode
# --------------------------------------------------------------------------- #


def _timed_get(base_url: str, path: str) -> float | None:
    start = time.monotonic()
    try:
        resp = requests.get(base_url + path, timeout=30)
        elapsed = time.monotonic() - start
        if resp.status_code >= 400:
            return None
        return elapsed
    except requests.RequestException:
        return None


def run_light(base_url: str, concurrency: int) -> None:
    tasks = [random.choice(LIGHT_ENDPOINTS) for _ in range(concurrency)]
    latencies: list[float] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_timed_get, base_url, path) for path in tasks]
        for fut in as_completed(futures):
            elapsed = fut.result()
            if elapsed is None:
                errors += 1
            else:
                latencies.append(elapsed)
    report(f"light mode, {concurrency} concurrent GETs (cheap read endpoints)", latencies, errors, len(tasks))


# --------------------------------------------------------------------------- #
# pipeline mode
# --------------------------------------------------------------------------- #


@dataclass
class PipelineResult:
    ok: bool
    elapsed: float | None = None
    final_status: str | None = None
    error: str | None = None


def _provision_token(base_url: str, index: int) -> str | None:
    """Phone signup -> verify (OTP is always echoed back, see auth/router.py's
    _dev_otp_field -- no real SMS provider is wired up), returns an access token.
    """
    phone = f"+9179{random.randint(10_000_000, 99_999_999)}"
    try:
        start_resp = requests.post(
            f"{base_url}/auth/signup/phone/start", json={"phone": phone}, timeout=30
        )
        start_resp.raise_for_status()
        otp = start_resp.json().get("dev_otp")
        if not otp:
            return None
        verify_resp = requests.post(
            f"{base_url}/auth/signup/phone/verify",
            json={
                "phone": phone,
                "otp": otp,
                "full_name": f"Load Test User {index}",
                "preferred_language": "en-IN",
            },
            timeout=30,
        )
        verify_resp.raise_for_status()
        return verify_resp.json().get("access_token")
    except requests.RequestException as exc:
        print(f"  [provision {index}] failed: {exc}")
        return None


def _run_one_case(base_url: str, token: str, index: int, poll_timeout: float) -> PipelineResult:
    headers = {"Authorization": f"Bearer {token}"}
    start = time.monotonic()
    try:
        sample = requests.get(f"{base_url}/api/sample-claim", headers=headers, timeout=30).json()
        claim = sample["claim"]
        create_resp = requests.post(f"{base_url}/api/cases", json=claim, headers=headers, timeout=30)
        create_resp.raise_for_status()
        case_id = create_resp.json()["case_id"]

        requests.post(f"{base_url}/api/cases/{case_id}/skip-response", headers=headers, timeout=30).raise_for_status()
        requests.post(f"{base_url}/api/cases/{case_id}/run", headers=headers, timeout=30).raise_for_status()

        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            status_resp = requests.get(f"{base_url}/api/cases/{case_id}", headers=headers, timeout=30)
            status_resp.raise_for_status()
            status = status_resp.json().get("status")
            if status in TERMINAL_STATUSES:
                return PipelineResult(ok=True, elapsed=time.monotonic() - start, final_status=status)
            time.sleep(1.5)
        return PipelineResult(ok=False, error=f"timed out after {poll_timeout}s (last status unknown)")
    except requests.RequestException as exc:
        return PipelineResult(ok=False, error=str(exc))


def run_pipeline(base_url: str, concurrency: int, poll_timeout: float) -> None:
    print(f"Provisioning {concurrency} test accounts (sequential, to respect OTP rate limits)...")
    tokens: list[str] = []
    for i in range(concurrency):
        token = _provision_token(base_url, i)
        if token:
            tokens.append(token)
        time.sleep(0.5)
    print(f"Provisioned {len(tokens)}/{concurrency} accounts.")
    if not tokens:
        print("No accounts provisioned -- aborting.")
        return

    print(f"Firing {len(tokens)} concurrent case runs through the real 5-agent pipeline...")
    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
        futures = [
            pool.submit(_run_one_case, base_url, token, i, poll_timeout)
            for i, token in enumerate(tokens)
        ]
        for fut in as_completed(futures):
            results.append(fut.result())

    latencies = [r.elapsed for r in results if r.ok and r.elapsed is not None]
    errors = sum(1 for r in results if not r.ok)
    for r in results:
        if not r.ok:
            print(f"  FAILED: {r.error}")
        else:
            print(f"  OK: {r.elapsed:.1f}s -> {r.final_status}")
    report(
        f"pipeline mode, {len(tokens)} concurrent full dispute runs (real Sarvam calls)",
        latencies,
        errors,
        len(results),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["light", "pipeline"])
    parser.add_argument("--base-url", default="https://diginyaya-backend.onrender.com")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--poll-timeout", type=float, default=180.0, help="pipeline mode only")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    print(f"Target: {base_url}  mode: {args.mode}  concurrency: {args.concurrency}")

    if args.mode == "light":
        run_light(base_url, args.concurrency)
    else:
        run_pipeline(base_url, args.concurrency, args.poll_timeout)


if __name__ == "__main__":
    main()
