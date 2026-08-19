"""Text-to-speech narration via Sarvam's Bulbul model.

DigiNyaya already uses four of Sarvam's five product lines (chat/JSON
generation, Document AI OCR, Speech-to-Text for audio evidence,
translation/LID for the Language Gateway) -- Bulbul was the one entirely
unused. Applied here to the actual product moment that benefits most:
hearing a resolution order or mediation proposal read aloud, in the
language the case was filed in, matters for claimants with limited
literacy or who simply prefer audio to a legal document.

Deliberately narrow scope: no cross-request audio chunk-stitching. Bulbul
charges per character and resolution/mediation text is realistically a
handful of sentences -- text over the practical single-clip limit is
truncated at a sentence boundary rather than built out with chunking
infrastructure for a case that essentially never happens in practice.
"""

from __future__ import annotations

import base64
import logging

from ..core.circuit_breaker import CircuitBreaker
from ..llm.config import config

logger = logging.getLogger("diginyaya.language.tts")

# Sarvam's own docs use "anushka" as the default demo voice; confirmed
# multilingual across the 11 languages this app supports. No per-language
# voice map for v1 -- one consistent narrator voice is the simpler and
# arguably more appropriate choice for an official case document anyway.
_DEFAULT_SPEAKER = "anushka"

# Practical single-clip comfort limit -- see module docstring. Resolution
# orders and mediation proposals are a few sentences; this is generous
# relative to that.
_MAX_TTS_CHARS = 1500

_breaker = CircuitBreaker(name="sarvam_tts")


def _truncate_at_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    window = text[:limit]
    best = -1
    for boundary in (". ", "? ", "! ", "। "):
        idx = window.rfind(boundary)
        if idx > limit // 2 and idx > best:
            best = idx + len(boundary)
    return window[:best] if best > 0 else window


def synthesize_speech(text: str, language_code: str, *, speaker: str = _DEFAULT_SPEAKER) -> bytes | None:
    """Return MP3 audio bytes narrating `text` in `language_code`, or None
    if unavailable/unconfigured/failed. Never raises -- callers should
    degrade to "audio unavailable" (e.g. hide the play control), matching
    every other Sarvam integration's fallback contract in this codebase.
    """
    text = (text or "").strip()
    if not text or not config.sarvam_api_key:
        return None
    if not _breaker.allow():
        logger.info("sarvam tts circuit breaker open; skipping call", extra={"event": "sarvam_tts_breaker_open"})
        return None
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None

    truncated = _truncate_at_sentence(text, _MAX_TTS_CHARS)
    try:
        client = SarvamAI(api_subscription_key=config.sarvam_api_key)
        response = client.text_to_speech.convert(
            text=truncated,
            language_code=language_code,
            speaker=speaker,
            model="bulbul:v2",
            output_audio_codec="mp3",
        )
        if not response.audios:
            _breaker.record_success()  # service responded; nothing came back to play
            return None
        audio_bytes = base64.b64decode(response.audios[0])
        _breaker.record_success()
        return audio_bytes
    except Exception as exc:
        logger.warning("sarvam tts call failed", extra={"event": "sarvam_tts_error", "error": str(exc)})
        _breaker.record_failure()
        return None
