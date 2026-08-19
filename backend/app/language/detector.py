"""Language detection for the Language Gateway.

Detects the language of incoming user text via Sarvam's Language
Identification (LID) endpoint (``/text-lid``) before the gateway decides
whether translation into the pipeline language is needed.

Design notes
------------
- Sarvam's ``/text-lid`` endpoint returns only ``language_code`` and
  ``script_code`` -- it does **not** return a confidence score. The
  ``confidence`` on :class:`DetectionResult` is therefore a heuristic
  derived from signal strength (how much alphabetic content the sample
  has), not a probability from the model itself. It exists so
  ``config.detection_confidence_threshold`` has something meaningful to
  compare against, and is intentionally conservative.
- ``/text-lid`` hard-caps input at 1000 characters, so detection always
  runs against a truncated sample, never the full document.
- Detection never raises for network/API failures. Every failure mode
  (disabled, empty input, low signal, API error, exhausted retries,
  unsupported/low-confidence result) degrades to a fallback
  :class:`DetectionResult` built from ``config.default_user_language`` so
  callers (the gateway) can always proceed without special-casing errors.

Settings are loaded from ``app.language.config``.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

import requests

from app.core.circuit_breaker import CircuitBreaker
from app.language.config import (
    LanguageConfig,
    config as language_config,
    is_pipeline_language,
    is_supported_language,
    normalize_language_code,
)

logger = logging.getLogger("diginyaya.language.detector")

# Own breaker, independent of translator.py's: LID (/text-lid) and
# translation (/translate) are different Sarvam endpoints with
# independently-failing uptime. Without this, a sustained outage means every
# inbound request pays the full retry_attempts x timeout loop below
# (~90s worst case at defaults) before falling back to default_user_language.
_breaker = CircuitBreaker(name="language_lid")

# Sarvam's LID endpoint hard-caps input at 1000 characters; detection only
# needs a representative sample, not the full document.
_MAX_LID_INPUT_CHARS = 1000

# Below this many alphabetic characters, LID has essentially nothing to
# work with (pure numbers/punctuation/emoji) -- Sarvam will still return
# *some* language_code in this case, but treating it as a real detection
# would be misleading, so we short-circuit to the fallback instead.
_MIN_ALPHABETIC_CHARS = 3

# Below this many alphabetic characters, short romanized tokens ("ok",
# "hi", "theek") are genuinely ambiguous between English and transliterated
# Indic text, so the heuristic confidence takes a penalty.
_SHORT_SAMPLE_ALPHA_CHARS = 8


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of a language-detection call. Always populated and usable,
    even when detection itself failed or was skipped -- see ``used_fallback``.
    """

    language_code: str  # normalized BCP-47 code, e.g. "hi-IN"
    script_code: str | None
    confidence: float  # heuristic 0.0-1.0; see module docstring
    is_confident: bool  # confidence >= config.detection_confidence_threshold
    is_supported: bool  # language_code in SUPPORTED_LANGUAGE_CODES
    is_pipeline_language: bool  # True when the detected language is English
    used_fallback: bool  # True when this result did not come from a trusted LID call
    fallback_reason: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LanguageDetector:
    """Detects the language of user-supplied text via Sarvam's LID endpoint."""

    def __init__(self, settings: LanguageConfig | None = None) -> None:
        self.settings = settings or language_config
        self._session = requests.Session()

    def detect(self, text: str) -> DetectionResult:
        """Detect the language of *text*.

        Falls back to ``config.default_user_language`` whenever detection is
        disabled, the input is empty, the sample carries too little signal,
        Sarvam is unreachable, or the result is unsupported/low-confidence.
        This method never raises for those cases -- callers always get a
        usable :class:`DetectionResult` back.
        """
        fallback_code = normalize_language_code(self.settings.default_user_language)

        if not self.settings.auto_detect:
            return self._fallback_result(fallback_code, reason="auto_detect_disabled")

        stripped = text.strip()
        if not stripped:
            return self._fallback_result(fallback_code, reason="empty_input")

        sample = self._sample_for_detection(stripped)
        if self._alphabetic_char_count(sample) < _MIN_ALPHABETIC_CHARS:
            return self._fallback_result(fallback_code, reason="insufficient_signal")

        response_data = self._call_sarvam_lid(sample)
        if response_data is None:
            return self._fallback_result(fallback_code, reason="api_unavailable")

        raw_code = response_data.get("language_code")
        script_code = response_data.get("script_code")
        request_id = response_data.get("request_id")

        if not raw_code:
            return self._fallback_result(
                fallback_code, reason="null_language_code", request_id=request_id
            )

        language_code = normalize_language_code(raw_code)
        confidence = self._estimate_confidence(sample)
        is_confident = confidence >= self.settings.detection_confidence_threshold
        supported = is_supported_language(language_code)

        if not is_confident or not supported:
            logger.info(
                "language detection below threshold or unsupported; using fallback",
                extra={
                    "event": "language_detection_fallback",
                    "detected_language": language_code,
                    "confidence": confidence,
                    "threshold": self.settings.detection_confidence_threshold,
                    "supported": supported,
                    "request_id": request_id,
                },
            )
            return self._fallback_result(
                fallback_code,
                reason="unsupported_language" if not supported else "low_confidence",
                request_id=request_id,
            )

        result = DetectionResult(
            language_code=language_code,
            script_code=script_code,
            confidence=confidence,
            is_confident=True,
            is_supported=True,
            is_pipeline_language=is_pipeline_language(language_code),
            used_fallback=False,
            request_id=request_id,
        )
        logger.debug(
            "language detected",
            extra={"event": "language_detected", **result.to_dict()},
        )
        return result

    # ------------------------------------------------------------------
    # Sampling / heuristics
    # ------------------------------------------------------------------

    def _sample_for_detection(self, text: str) -> str:
        """Normalize and truncate *text* to fit Sarvam's 1000-char LID limit."""
        normalized = unicodedata.normalize("NFC", text)
        if len(normalized) <= _MAX_LID_INPUT_CHARS:
            return normalized

        truncated = normalized[:_MAX_LID_INPUT_CHARS]
        last_space = truncated.rfind(" ")
        # Prefer cutting on a whitespace boundary so we don't split a word
        # right at the limit, as long as it doesn't throw away too much.
        if last_space > _MAX_LID_INPUT_CHARS // 2:
            truncated = truncated[:last_space]
        return truncated

    @staticmethod
    def _alphabetic_char_count(text: str) -> int:
        return sum(1 for ch in text if ch.isalpha())

    @staticmethod
    def _estimate_confidence(sample: str) -> float:
        """Heuristic confidence proxy, since Sarvam's LID response carries none.

        More alphabetic content makes a wrong detection less likely; very
        short alphabetic samples are ambiguous (e.g. romanized greetings
        that could be English or transliterated Hindi), so they take a
        penalty. This is a proxy for signal strength, not a real
        model-reported probability.
        """
        alpha_chars = LanguageDetector._alphabetic_char_count(sample)
        if alpha_chars == 0:
            return 0.0

        length_component = min(alpha_chars / 20.0, 1.0)
        ambiguity_penalty = 0.3 if alpha_chars < _SHORT_SAMPLE_ALPHA_CHARS else 0.0
        confidence = 0.7 + 0.3 * length_component - ambiguity_penalty
        return max(0.0, min(1.0, confidence))

    # ------------------------------------------------------------------
    # Sarvam call
    # ------------------------------------------------------------------

    def _call_sarvam_lid(self, sample: str) -> dict[str, Any] | None:
        if not self.settings.sarvam_api_key:
            logger.warning(
                "sarvam api key not configured; skipping language detection",
                extra={"event": "language_detection_no_api_key"},
            )
            return None

        if not _breaker.allow():
            logger.info(
                "language detection circuit breaker open; skipping call",
                extra={"event": "language_detection_breaker_open"},
            )
            return None

        headers = {
            "api-subscription-key": self.settings.sarvam_api_key,
            "Content-Type": "application/json",
        }
        payload = {"input": sample}

        attempts = max(1, self.settings.retry_attempts + 1)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.post(
                    self.settings.sarvam_lid_url,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.timeout,
                )
                response.raise_for_status()
                _breaker.record_success()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "sarvam LID call failed",
                    extra={
                        "event": "language_detection_api_error",
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "error": str(exc),
                    },
                )
                if attempt < attempts:
                    time.sleep(self.settings.retry_delay * attempt)

        _breaker.record_failure()
        logger.error(
            "sarvam LID call exhausted retries; falling back to default language",
            extra={
                "event": "language_detection_exhausted",
                "attempts": attempts,
                "error": str(last_error) if last_error else None,
            },
        )
        return None

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_result(
        self,
        fallback_code: str,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> DetectionResult:
        logger.debug(
            "language detection fallback used",
            extra={
                "event": "language_detection_fallback_used",
                "reason": reason,
                "fallback_language": fallback_code,
            },
        )
        return DetectionResult(
            language_code=fallback_code,
            script_code=None,
            confidence=0.0,
            is_confident=False,
            is_supported=is_supported_language(fallback_code),
            is_pipeline_language=is_pipeline_language(fallback_code),
            used_fallback=True,
            fallback_reason=reason,
            request_id=request_id,
        )


_default_detector: LanguageDetector | None = None
_default_detector_lock = threading.Lock()


def get_language_detector(settings: LanguageConfig | None = None) -> LanguageDetector:
    """Return the process-wide language detector singleton."""
    global _default_detector
    if settings is not None:
        return LanguageDetector(settings)

    if _default_detector is None:
        with _default_detector_lock:
            if _default_detector is None:
                _default_detector = LanguageDetector()
    return _default_detector


def detect_language(text: str) -> DetectionResult:
    """Convenience wrapper around the singleton detector."""
    return get_language_detector().detect(text)