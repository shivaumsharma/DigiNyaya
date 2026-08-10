"""Translation for the Language Gateway.

Calls Sarvam's translation endpoint (``/translate``, Mayura v1) to convert
text between a user's language and the pipeline language (English) in
either direction. This is the module :mod:`app.language.gateway` delegates
to for the actual network call; the gateway itself only decides *whether*
a call is needed (already English? cache hit?) and never talks to Sarvam
directly.

Design notes
------------
- Sarvam's Mayura model hard-caps input at ``config.max_chunk_chars``
  (1000) characters per request, same limit ``detector.py`` already works
  around for LID. Unlike LID -- which only needs a representative sample --
  translation must cover the *entire* input, so long text is split into
  multiple chunks, each translated independently and rejoined, rather than
  truncated.
- Chunk boundaries prefer paragraph breaks, then sentence-ending
  punctuation (including the Devanagari/Indic danda "।"), then whitespace,
  falling back to a hard cut only when no good boundary exists in the
  first half of the window. Rejoining uses a single space between chunks;
  this is an approximation, not a byte-for-byte whitespace round-trip --
  independently translated chunks were never going to preserve exact
  source spacing across a request boundary anyway.
- Translation is best-effort but never raises for network/API failures,
  same philosophy as ``detector.py``. Every failure mode (no API key,
  identical source/target, empty input, API error, exhausted retries)
  degrades to a fallback :class:`TranslationResult` that carries the
  *original* text through untranslated, flagged via ``used_fallback`` /
  ``fallback_reason``, so a caller can always proceed -- worst case the
  gateway hands agents (or the user) untranslated text instead of crashing
  the pipeline.

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
    normalize_language_code,
)

logger = logging.getLogger("diginyaya.language.translator")

# Own breaker, independent of detector.py's -- see that module's comment on
# why LID and translation don't share one. Without this, a sustained Sarvam
# outage means every inbound/outbound translation on the live request path
# pays the full retry_attempts x timeout loop below (~90s worst case at
# defaults) before degrading to untranslated passthrough text.
_breaker = CircuitBreaker(name="language_translate")

# Sarvam Mayura's hard per-request input cap is exposed as
# config.max_chunk_chars (default 1000) so it can be tuned without a code
# change if Sarvam's limit ever moves.
_SENTENCE_BOUNDARIES: tuple[str, ...] = (". ", "? ", "! ", "। ", "।")


@dataclass(frozen=True)
class TranslationResult:
    """Outcome of a single translate() call. Always populated and usable,
    even when the underlying API call failed or was skipped -- see
    ``used_fallback``.
    """

    translated_text: str
    source_lang: str
    target_lang: str
    provider: str = "sarvam"
    model: str | None = None
    request_id: str | None = None
    was_chunked: bool = False
    chunk_count: int = 1
    used_fallback: bool = False
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Translator:
    """Translates text via Sarvam's ``/translate`` endpoint."""

    def __init__(self, settings: LanguageConfig | None = None) -> None:
        self.settings = settings or language_config
        self._session = requests.Session()

    def translate(
        self,
        text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> TranslationResult:
        """Translate *text* from ``source_lang`` to ``target_lang``.

        Never raises. Falls back to returning *text* unchanged (with
        ``used_fallback=True``) when there is nothing to translate, the
        languages are identical, no API key is configured, or Sarvam is
        unreachable after retries.
        """
        normalized_source = normalize_language_code(source_lang)
        normalized_target = normalize_language_code(target_lang)

        if not text or not text.strip():
            return self._fallback_result(
                text, normalized_source, normalized_target, reason="empty_input"
            )

        if normalized_source.lower() == normalized_target.lower():
            return self._fallback_result(
                text,
                normalized_source,
                normalized_target,
                reason="identical_language",
                provider="passthrough",
            )

        if not self.settings.sarvam_api_key:
            logger.warning(
                "sarvam api key not configured; skipping translation",
                extra={"event": "translation_no_api_key"},
            )
            return self._fallback_result(
                text, normalized_source, normalized_target, reason="no_api_key"
            )

        chunks = self._chunk_text(text)
        translated_parts: list[str] = []
        last_request_id: str | None = None

        for index, chunk in enumerate(chunks):
            response_data = self._call_sarvam_translate(
                chunk, normalized_source, normalized_target
            )
            if response_data is None:
                logger.error(
                    "translation failed; falling back to original text",
                    extra={
                        "event": "translation_fallback_used",
                        "reason": "api_unavailable",
                        "source_lang": normalized_source,
                        "target_lang": normalized_target,
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                    },
                )
                return self._fallback_result(
                    text,
                    normalized_source,
                    normalized_target,
                    reason="api_unavailable",
                    was_chunked=len(chunks) > 1,
                    chunk_count=len(chunks),
                )

            translated_chunk = response_data.get("translated_text")
            if not translated_chunk:
                logger.error(
                    "translation response missing translated_text; falling back",
                    extra={
                        "event": "translation_fallback_used",
                        "reason": "null_translated_text",
                        "source_lang": normalized_source,
                        "target_lang": normalized_target,
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                    },
                )
                return self._fallback_result(
                    text,
                    normalized_source,
                    normalized_target,
                    reason="null_translated_text",
                    was_chunked=len(chunks) > 1,
                    chunk_count=len(chunks),
                )

            translated_parts.append(translated_chunk)
            last_request_id = response_data.get("request_id") or last_request_id

        joined = self._join_chunks(translated_parts)
        logger.info(
            "translation succeeded",
            extra={
                "event": "translation_succeeded",
                "source_lang": normalized_source,
                "target_lang": normalized_target,
                "chunk_count": len(chunks),
                "request_id": last_request_id,
            },
        )
        return TranslationResult(
            translated_text=joined,
            source_lang=normalized_source,
            target_lang=normalized_target,
            provider="sarvam",
            model=self.settings.translation_model,
            request_id=last_request_id,
            was_chunked=len(chunks) > 1,
            chunk_count=len(chunks),
            used_fallback=False,
        )

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str) -> list[str]:
        """Split *text* into pieces at or under ``config.max_chunk_chars``,
        preferring paragraph/sentence/whitespace boundaries over hard cuts.
        """
        limit = self.settings.max_chunk_chars
        normalized = unicodedata.normalize("NFC", text.strip())
        if len(normalized) <= limit:
            return [normalized]

        chunks: list[str] = []
        remaining = normalized
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            window = remaining[:limit]
            split_at = self._find_split_point(window)
            if split_at <= 0:
                split_at = limit  # no good boundary in the first half; hard cut

            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _find_split_point(window: str) -> int:
        """Return an index into *window* to split on, preferring (in
        order) a paragraph break, a sentence ending, or whitespace -- only
        when that boundary falls in the second half of the window, so a
        chunk is never made needlessly short.
        """
        half = len(window) // 2

        para_break = window.rfind("\n\n")
        if para_break > half:
            return para_break + 2

        best = -1
        for boundary in _SENTENCE_BOUNDARIES:
            idx = window.rfind(boundary)
            if idx > half:
                candidate = idx + len(boundary)
                if candidate > best:
                    best = candidate
        if best > 0:
            return best

        space = window.rfind(" ")
        if space > half:
            return space + 1

        return 0

    @staticmethod
    def _join_chunks(parts: list[str]) -> str:
        return " ".join(part.strip() for part in parts if part.strip())

    # ------------------------------------------------------------------
    # Sarvam call
    # ------------------------------------------------------------------

    def _call_sarvam_translate(
        self,
        chunk: str,
        source_lang: str,
        target_lang: str,
    ) -> dict[str, Any] | None:
        if not _breaker.allow():
            logger.info(
                "translation circuit breaker open; skipping call",
                extra={"event": "translation_breaker_open"},
            )
            return None

        headers = {
            "api-subscription-key": self.settings.sarvam_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "input": chunk,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "model": self.settings.translation_model,
        }

        attempts = max(1, self.settings.retry_attempts + 1)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.post(
                    self.settings.sarvam_translate_url,
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
                    "sarvam translate call failed",
                    extra={
                        "event": "translation_api_error",
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "error": str(exc),
                    },
                )
                if attempt < attempts:
                    time.sleep(self.settings.retry_delay * attempt)

        _breaker.record_failure()
        logger.error(
            "sarvam translate call exhausted retries",
            extra={
                "event": "translation_exhausted",
                "attempts": attempts,
                "error": str(last_error) if last_error else None,
            },
        )
        return None

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_result(
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        reason: str,
        provider: str = "sarvam",
        was_chunked: bool = False,
        chunk_count: int = 1,
    ) -> TranslationResult:
        logger.debug(
            "translation fallback used",
            extra={
                "event": "translation_fallback_used",
                "reason": reason,
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
        )
        return TranslationResult(
            translated_text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=provider,
            model=None,
            request_id=None,
            was_chunked=was_chunked,
            chunk_count=chunk_count,
            used_fallback=True,
            fallback_reason=reason,
        )


_default_translator: Translator | None = None
_default_translator_lock = threading.Lock()


def get_translator(settings: LanguageConfig | None = None) -> Translator:
    """Return the process-wide translator singleton."""
    global _default_translator
    if settings is not None:
        return Translator(settings)

    if _default_translator is None:
        with _default_translator_lock:
            if _default_translator is None:
                _default_translator = Translator()
    return _default_translator


def translate_text(text: str, *, source_lang: str, target_lang: str) -> TranslationResult:
    """Convenience wrapper around the singleton translator."""
    return get_translator().translate(text, source_lang=source_lang, target_lang=target_lang)