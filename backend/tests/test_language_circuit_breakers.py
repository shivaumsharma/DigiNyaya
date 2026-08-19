"""Regression tests proving app.language.detector and app.language.translator
are actually wired to their circuit breakers -- i.e. a sustained Sarvam
LID/translate outage fails fast instead of paying the full
retry_attempts x timeout loop on every single call (added during the
production-readiness review, 2026-08-08: LID and translation run on the
live request path via the Language Gateway, unlike OCR/STT which only run
in background job threads).

Run with (from backend/):
    python -m unittest tests.test_language_circuit_breakers -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.circuit_breaker import CircuitBreaker  # noqa: E402
from app.language import detector, translator  # noqa: E402
from app.language.config import LanguageConfig  # noqa: E402


def _settings(**overrides) -> LanguageConfig:
    defaults = dict(sarvam_api_key="test-key", retry_attempts=0, retry_delay=0.0, timeout=1)
    defaults.update(overrides)
    return LanguageConfig(**defaults)


class TestDetectorCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self._breaker = CircuitBreaker(name="test_lid")
        self._patch = patch.object(detector, "_breaker", self._breaker)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_open_breaker_skips_network_call(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        det = detector.LanguageDetector(settings=_settings())
        with patch.object(det._session, "post") as mock_post:
            result = det._call_sarvam_lid("hello world")

        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_closed_breaker_calls_network_and_records_success(self):
        det = detector.LanguageDetector(settings=_settings())
        mock_response = MagicMock()
        mock_response.json.return_value = {"language_code": "hi-IN", "script_code": "Deva"}
        mock_response.raise_for_status.return_value = None
        with patch.object(det._session, "post", return_value=mock_response) as mock_post:
            result = det._call_sarvam_lid("नमस्ते")

        self.assertEqual(result["language_code"], "hi-IN")
        mock_post.assert_called_once()
        self.assertTrue(self._breaker.allow())

    def test_repeated_failures_open_the_breaker(self):
        det = detector.LanguageDetector(settings=_settings())
        with patch.object(det._session, "post", side_effect=requests.ConnectionError("down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = det._call_sarvam_lid("hello world")
                self.assertIsNone(result)

        self.assertFalse(self._breaker.allow())


class TestTranslatorCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self._breaker = CircuitBreaker(name="test_translate")
        self._patch = patch.object(translator, "_breaker", self._breaker)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_open_breaker_skips_network_call(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        t = translator.Translator(settings=_settings())
        with patch.object(t._session, "post") as mock_post:
            result = t._call_sarvam_translate("hello", "en-IN", "hi-IN")

        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_open_breaker_makes_translate_fall_back_without_network(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        t = translator.Translator(settings=_settings())
        with patch.object(t._session, "post") as mock_post:
            result = t.translate("hello there", source_lang="en-IN", target_lang="hi-IN")

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.translated_text, "hello there")
        mock_post.assert_not_called()

    def test_closed_breaker_calls_network_and_records_success(self):
        t = translator.Translator(settings=_settings())
        mock_response = MagicMock()
        mock_response.json.return_value = {"translated_text": "नमस्ते", "request_id": "req-1"}
        mock_response.raise_for_status.return_value = None
        with patch.object(t._session, "post", return_value=mock_response) as mock_post:
            result = t._call_sarvam_translate("hello", "en-IN", "hi-IN")

        self.assertEqual(result["translated_text"], "नमस्ते")
        mock_post.assert_called_once()
        self.assertTrue(self._breaker.allow())

    def test_repeated_failures_open_the_breaker(self):
        t = translator.Translator(settings=_settings())
        with patch.object(t._session, "post", side_effect=requests.ConnectionError("down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = t._call_sarvam_translate("hello", "en-IN", "hi-IN")
                self.assertIsNone(result)

        self.assertFalse(self._breaker.allow())


if __name__ == "__main__":
    unittest.main()
