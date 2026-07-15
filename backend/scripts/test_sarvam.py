"""Standalone Sarvam connectivity check — run from the `backend` folder.

Usage:
    python scripts/test_sarvam.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.llm.config import config
from app.llm.providers.sarvam import SarvamProvider


def main() -> None:
    print("=" * 60)
    print(f"Configured provider : {config.provider}")
    print(f"Base URL            : {config.sarvam_base_url}")
    print(f"Key present         : {'yes' if config.sarvam_api_key else 'NO — SARVAM_API_KEY is empty!'}")
    print(f"Fast model          : {config.sarvam_fast_model}")
    print(f"Reasoning model     : {config.sarvam_reasoning_model}")
    print("=" * 60)

    if not config.sarvam_api_key:
        print("\nStopping here — no API key configured. Check your .env.")
        return

    provider = SarvamProvider()

    print("\n[1/3] Checking is_available() (GET /models)...")
    try:
        available = provider.is_available()
        print(f"      -> {'OK, reachable' if available else 'responded but not OK (bad key / quota?)'}")
    except Exception as e:
        print(f"      -> FAILED: {e!r}")
        return

    if not available:
        print("\nStopping here — provider reports unavailable. Check your API key and account status.")
        return

    print("\n[2/3] Trying a real chat completion (fast model)...")
    try:
        out = provider.generate(
            "Reply with exactly the word: PONG",
            system="You are a terse test assistant.",
            model="classification",  # maps to sarvam_fast_model via MODEL_MAP
            max_tokens=10,
        )
        print(f"      -> Response: {out!r}")
    except Exception as e:
        print(f"      -> FAILED: {e!r}")
        return

    print("\n[3/3] Trying structured JSON generation (reasoning model)...")
    try:
        data = provider.generate_json(
            'Return JSON only: {"status": "ok"}',
            system="Return only valid JSON, nothing else.",
            model="analysis",  # maps to sarvam_reasoning_model
            max_tokens=30,
        )
        print(f"      -> Parsed JSON: {data!r}")
    except Exception as e:
        print(f"      -> FAILED: {e!r}")
        return

    print("\nAll checks passed — Sarvam is genuinely live and will be used by Agents 3/4/5.")


if __name__ == "__main__":
    main()
