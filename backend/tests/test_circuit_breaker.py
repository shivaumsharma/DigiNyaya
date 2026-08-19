"""Unit tests for app.core.circuit_breaker.CircuitBreaker, the shared class
extracted from app.llm.client's original private _CircuitBreaker so every
direct Sarvam call site (LID, translation, Document AI OCR, Speech-to-Text,
in addition to chat/JSON generation) can get the same fail-fast protection.

See tests/test_llm_circuit_breaker.py for the LLM-client-specific wiring
tests (unchanged by this extraction); this file tests the generic class in
isolation.

Run with (from backend/):
    python -m unittest tests.test_circuit_breaker -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.circuit_breaker import CircuitBreaker  # noqa: E402


class TestCircuitBreaker(unittest.TestCase):
    def _breaker(self, **kwargs) -> CircuitBreaker:
        return CircuitBreaker(name="test", failure_threshold=3, cooldown_seconds=30.0, **kwargs)

    def test_allows_calls_before_any_failure(self):
        self.assertTrue(self._breaker().allow())

    def test_stays_closed_below_failure_threshold(self):
        breaker = self._breaker()
        breaker.record_failure()
        breaker.record_failure()
        self.assertTrue(breaker.allow())

    def test_opens_after_threshold_consecutive_failures(self):
        breaker = self._breaker()
        for _ in range(3):
            breaker.record_failure()
        self.assertFalse(breaker.allow())

    def test_success_resets_failure_count(self):
        breaker = self._breaker()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.record_failure()
        self.assertTrue(breaker.allow())

    def test_instances_are_independent(self):
        # Each protected Sarvam sub-API (OCR, STT, LID, translation) owns
        # its own breaker precisely so one endpoint's outage doesn't trip
        # an unrelated one -- verify that isolation directly.
        ocr_breaker = self._breaker()
        stt_breaker = self._breaker()
        for _ in range(3):
            ocr_breaker.record_failure()
        self.assertFalse(ocr_breaker.allow())
        self.assertTrue(stt_breaker.allow())

    def test_custom_threshold_and_cooldown_respected(self):
        breaker = CircuitBreaker(name="custom", failure_threshold=1, cooldown_seconds=30.0)
        breaker.record_failure()
        self.assertFalse(breaker.allow())


if __name__ == "__main__":
    unittest.main()
