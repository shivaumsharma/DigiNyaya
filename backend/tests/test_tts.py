"""Unit tests for app.language.tts.synthesize_speech -- Sarvam's Bulbul
text-to-speech, added 2026-08-11 to read a case's resolution/mediation
proposal aloud (the one Sarvam product line -- of the five the app's
Startup Program credits cover -- that had no integration yet).

Mirrors tests/test_document_extraction.py's convention of monkeypatching
the sarvamai SDK constructor rather than hitting the real network.

Run with (from backend/):
    python -m unittest tests.test_tts -v
"""
from __future__ import annotations

import base64
import dataclasses
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.circuit_breaker import CircuitBreaker  # noqa: E402
from app.language import tts  # noqa: E402


def _fake_response(*, audios):
    resp = mock.MagicMock()
    resp.audios = audios
    return resp


class TestSynthesizeSpeech(unittest.TestCase):
    def setUp(self):
        self._breaker = CircuitBreaker(name="test_tts")
        self._breaker_patch = mock.patch.object(tts, "_breaker", self._breaker)
        self._breaker_patch.start()
        self._key_patch = mock.patch.object(
            tts, "config", dataclasses.replace(tts.config, sarvam_api_key="test-key")
        )
        self._key_patch.start()

    def tearDown(self):
        self._breaker_patch.stop()
        self._key_patch.stop()

    def test_no_api_key_returns_none_without_calling_the_network(self):
        with mock.patch.object(tts, "config", dataclasses.replace(tts.config, sarvam_api_key=None)):
            with mock.patch("sarvamai.SarvamAI") as mock_client_cls:
                result = tts.synthesize_speech("Hello", "en-IN")
        self.assertIsNone(result)
        mock_client_cls.assert_not_called()

    def test_empty_text_returns_none_without_calling_the_network(self):
        with mock.patch("sarvamai.SarvamAI") as mock_client_cls:
            result = tts.synthesize_speech("   ", "en-IN")
        self.assertIsNone(result)
        mock_client_cls.assert_not_called()

    def test_successful_call_returns_decoded_audio_bytes(self):
        raw_audio = b"fake-mp3-bytes"
        encoded = base64.b64encode(raw_audio).decode("ascii")
        mock_client = mock.MagicMock()
        mock_client.text_to_speech.convert.return_value = _fake_response(audios=[encoded])
        with mock.patch("sarvamai.SarvamAI", return_value=mock_client):
            result = tts.synthesize_speech("Order: pay Rs. 5000.", "hi-IN")

        self.assertEqual(result, raw_audio)
        kwargs = mock_client.text_to_speech.convert.call_args.kwargs
        self.assertEqual(kwargs["language_code"], "hi-IN")
        self.assertEqual(kwargs["model"], "bulbul:v2")
        self.assertTrue(self._breaker.allow())

    def test_no_audios_in_response_returns_none_and_counts_as_success(self):
        mock_client = mock.MagicMock()
        mock_client.text_to_speech.convert.return_value = _fake_response(audios=[])
        with mock.patch("sarvamai.SarvamAI", return_value=mock_client):
            result = tts.synthesize_speech("Some text", "en-IN")
        self.assertIsNone(result)
        self.assertTrue(self._breaker.allow())  # service responded fine -- not an availability failure

    def test_exception_records_failure_and_returns_none(self):
        with mock.patch("sarvamai.SarvamAI", side_effect=RuntimeError("network down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = tts.synthesize_speech("Some text", "en-IN")
                self.assertIsNone(result)
        self.assertFalse(self._breaker.allow())

    def test_open_breaker_skips_client_construction(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()
        with mock.patch("sarvamai.SarvamAI") as mock_client_cls:
            result = tts.synthesize_speech("Some text", "en-IN")
        self.assertIsNone(result)
        mock_client_cls.assert_not_called()


class TestTruncateAtSentence(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(tts._truncate_at_sentence("Hello there.", 1500), "Hello there.")

    def test_long_text_is_cut_at_a_sentence_boundary(self):
        text = "First sentence. " + ("Filler word. " * 200) + "Last sentence."
        truncated = tts._truncate_at_sentence(text, 100)
        self.assertLessEqual(len(truncated), 100)
        self.assertTrue(truncated.endswith(". ") or truncated.endswith("."))

    def test_no_good_boundary_falls_back_to_a_hard_cut(self):
        text = "a" * 3000
        truncated = tts._truncate_at_sentence(text, 100)
        self.assertEqual(len(truncated), 100)


if __name__ == "__main__":
    unittest.main()
