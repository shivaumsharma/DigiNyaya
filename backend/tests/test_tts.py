"""Unit tests for app.language.tts -- Sarvam Bulbul text-to-speech
narration, added to read a resolution order or mediation proposal aloud
in the citizen's filing language.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.circuit_breaker import CircuitBreaker
from app.language import tts as tts_module
from app.language.tts import _truncate_at_sentence, synthesize_speech


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch):
    breaker = CircuitBreaker(name="test_tts")
    monkeypatch.setattr(tts_module, "_breaker", breaker)
    return breaker


def _config_with_key(monkeypatch, key="test-key"):
    import dataclasses
    monkeypatch.setattr(tts_module, "config", dataclasses.replace(tts_module.config, sarvam_api_key=key))


class TestTruncateAtSentence:
    def test_short_text_unchanged(self):
        assert _truncate_at_sentence("Hello there.", 100) == "Hello there."

    def test_long_text_cut_at_sentence_boundary(self):
        text = "First sentence here. " * 100
        result = _truncate_at_sentence(text, 50)
        assert result.endswith(". ") or result.endswith(".")
        assert len(result) <= 50

    def test_no_good_boundary_hard_cuts(self):
        text = "a" * 200
        result = _truncate_at_sentence(text, 50)
        assert len(result) == 50


class TestSynthesizeSpeech:
    def test_empty_text_returns_none(self, monkeypatch):
        _config_with_key(monkeypatch)
        assert synthesize_speech("", "en-IN") is None
        assert synthesize_speech("   ", "en-IN") is None

    def test_no_api_key_returns_none(self, monkeypatch):
        _config_with_key(monkeypatch, key=None)
        assert synthesize_speech("Hello", "en-IN") is None

    def test_open_breaker_skips_client_construction(self, monkeypatch, _fresh_breaker):
        _config_with_key(monkeypatch)
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            _fresh_breaker.record_failure()

        with patch("sarvamai.SarvamAI") as mock_client_cls:
            result = synthesize_speech("Hello there", "en-IN")

        assert result is None
        mock_client_cls.assert_not_called()

    def test_successful_call_returns_decoded_audio_bytes(self, monkeypatch, _fresh_breaker):
        _config_with_key(monkeypatch)
        raw_audio = b"\x00\x01\x02fake-mp3-bytes"
        encoded = base64.b64encode(raw_audio).decode("ascii")
        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = MagicMock(audios=[encoded])

        with patch("sarvamai.SarvamAI", return_value=mock_client):
            result = synthesize_speech("Hello there", "hi-IN")

        assert result == raw_audio
        mock_client.text_to_speech.convert.assert_called_once()
        kwargs = mock_client.text_to_speech.convert.call_args.kwargs
        assert kwargs["language_code"] == "hi-IN"
        assert kwargs["text"] == "Hello there"
        assert kwargs["model"] == "bulbul:v2"
        assert _fresh_breaker.allow() is True

    def test_empty_audios_list_returns_none_without_failing_breaker(self, monkeypatch, _fresh_breaker):
        _config_with_key(monkeypatch)
        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = MagicMock(audios=[])

        with patch("sarvamai.SarvamAI", return_value=mock_client):
            result = synthesize_speech("Hello there", "en-IN")

        assert result is None
        assert _fresh_breaker.allow() is True  # service responded fine -- not a breaker failure

    def test_network_failure_returns_none_and_records_breaker_failure(self, monkeypatch, _fresh_breaker):
        _config_with_key(monkeypatch)
        with patch("sarvamai.SarvamAI", side_effect=requests.ConnectionError("down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = synthesize_speech("Hello there", "en-IN")
                assert result is None

        assert _fresh_breaker.allow() is False

    def test_long_text_is_truncated_before_the_call(self, monkeypatch, _fresh_breaker):
        _config_with_key(monkeypatch)
        long_text = "This is a sentence. " * 200
        mock_client = MagicMock()
        mock_client.text_to_speech.convert.return_value = MagicMock(audios=[base64.b64encode(b"x").decode()])

        with patch("sarvamai.SarvamAI", return_value=mock_client):
            synthesize_speech(long_text, "en-IN")

        sent_text = mock_client.text_to_speech.convert.call_args.kwargs["text"]
        assert len(sent_text) <= tts_module._MAX_TTS_CHARS
