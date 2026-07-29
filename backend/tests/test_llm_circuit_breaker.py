"""Regression tests for the circuit breaker around LLM provider calls
(app.llm.client._CircuitBreaker).

Without this, a Sarvam outage means every agent in every concurrent case
pays the full provider timeout (tens of seconds) on every call before
falling back -- the breaker's whole point is failing fast instead, once a
real outage is established, without changing the existing None/empty
degrade contract callers already rely on.

Run with (from backend/):
    python -m unittest tests.test_llm_circuit_breaker -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import client  # noqa: E402


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        # Fresh breaker per test -- state is process-global in the real
        # module, so tests would otherwise leak failure counts into each
        # other depending on run order.
        self._breaker = client._CircuitBreaker()
        self._patch = patch.object(client, "_breaker", self._breaker)
        self._patch.start()
        self._disabled_patch = patch.object(client, "_llm_disabled", return_value=False)
        self._disabled_patch.start()

    def tearDown(self):
        self._patch.stop()
        self._disabled_patch.stop()

    def test_allows_calls_before_any_failure(self):
        self.assertTrue(self._breaker.allow())

    def test_stays_closed_below_failure_threshold(self):
        self._breaker.record_failure()
        self._breaker.record_failure()
        self.assertTrue(self._breaker.allow())  # 2 failures, threshold is 3

    def test_opens_after_threshold_consecutive_failures(self):
        for _ in range(client._CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()
        self.assertFalse(self._breaker.allow())

    def test_success_resets_failure_count(self):
        self._breaker.record_failure()
        self._breaker.record_failure()
        self._breaker.record_success()
        self._breaker.record_failure()
        self._breaker.record_failure()
        # 2 failures since the reset -- still below threshold, so still closed.
        self.assertTrue(self._breaker.allow())

    def test_open_breaker_short_circuits_generate_without_calling_provider(self):
        for _ in range(client._CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        mock_provider = MagicMock()
        with patch.object(client, "get_provider", return_value=mock_provider):
            result = client.generate("some prompt")

        self.assertIsNone(result)
        mock_provider.generate.assert_not_called()

    def test_closed_breaker_calls_provider_and_records_success(self):
        mock_provider = MagicMock()
        mock_provider.generate.return_value = "hello"
        with patch.object(client, "get_provider", return_value=mock_provider):
            result = client.generate("some prompt")

        self.assertEqual(result, "hello")
        mock_provider.generate.assert_called_once()
        self.assertTrue(self._breaker.allow())

    def test_provider_exception_records_failure_and_still_degrades_to_none(self):
        mock_provider = MagicMock()
        mock_provider.generate.side_effect = RuntimeError("boom")
        with patch.object(client, "get_provider", return_value=mock_provider):
            for _ in range(client._CircuitBreaker.FAILURE_THRESHOLD):
                result = client.generate("some prompt")
                self.assertIsNone(result)

        self.assertFalse(self._breaker.allow())


if __name__ == "__main__":
    unittest.main()
