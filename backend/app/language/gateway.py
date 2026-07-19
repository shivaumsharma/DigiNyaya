"""Language Gateway for DigiNyaya.

The single entry/exit point for all cross-language traffic around the
English-only multi-agent pipeline (Ingestion -> Research -> Analysis ->
Mediation -> Resolution). Callers use this module to:

  1. Translate incoming user text into the pipeline language (English) via
     :meth:`LanguageGateway.to_pipeline_language`, *before* handing it to
     the agents.
  2. Translate pipeline/agent output back into the user's preferred
     language via :meth:`LanguageGateway.to_user_language`, *after* the
     agents have run.

This module does not modify or wrap agent behaviour itself -- it only
prepares English input for them and localizes English output afterward.
Callers (API endpoints, ingestion) are responsible for actually calling the
agents in between the two gateway calls, and for persisting whatever of the
original/pipeline text they need (see module docstring notes in the calling
code for the "store both original and normalized text" requirement -- that
is a storage-layer concern, not this module's).

Composition, not reimplementation:
  - Detection is delegated to :mod:`app.language.detector`.
  - Translation is delegated to :mod:`app.language.translator`.
  - Caching is delegated to :mod:`app.language.cache`.

This keeps the gateway itself a thin, easily-testable orchestrator: given a
disabled feature flag, an already-English input, or a cache hit, it should
be obvious from reading ``to_pipeline_language``/``to_user_language`` alone
why no network call happened.

Settings are loaded from ``app.language.config``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any

from app.language.cache import TranslationCache, get_translation_cache
from app.language.config import (
    SUPPORTED_LANGUAGE_CODES,
    LanguageConfig,
    config as language_config,
    is_pipeline_language,
    is_supported_language,
    normalize_language_code,
)
from app.language.detector import (
    DetectionResult,
    LanguageDetector,
    get_language_detector,
)
from app.language.translator import (
    Translator,
    TranslationResult,
    get_translator,
)

logger = logging.getLogger("diginyaya.language.gateway")


class UnsupportedLanguageError(ValueError):
    """Raised when a caller explicitly requests a language the gateway does
    not support (e.g. an unrecognized ``target_lang`` for outbound
    localization). Callers that only ever pass values sourced from
    ``SUPPORTED_LANGUAGES``/the frontend selector should never hit this.
    """


@dataclass(frozen=True)
class InboundResult:
    """Outcome of translating user-supplied text into the pipeline language.

    ``pipeline_text`` is always safe to hand to the agents: it is English
    whenever ``was_translated`` is True, and was already English (or the
    gateway is disabled) when it is False.
    """

    original_text: str
    pipeline_text: str
    source_language: str  # normalized BCP-47, e.g. "hi-IN"
    detection: DetectionResult
    was_translated: bool
    used_cache: bool
    provider: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detection"] = self.detection.to_dict()
        return data


@dataclass(frozen=True)
class OutboundResult:
    """Outcome of localizing pipeline (English) output for the user."""

    pipeline_text: str
    localized_text: str
    target_language: str  # normalized BCP-47, e.g. "hi-IN"
    was_translated: bool
    used_cache: bool
    provider: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LanguageGateway:
    """Thin orchestrator wiring detection + translation + caching together.

    Instances are safe to share across requests/threads; the heavy lifting
    (locking, singletons) already lives in the wrapped detector/translator/
    cache. Construct a custom instance (e.g. in tests) by passing explicit
    collaborators; otherwise use :func:`get_language_gateway` for the
    process-wide singleton, same pattern as the other ``app.language``
    modules.
    """

    def __init__(
        self,
        settings: LanguageConfig | None = None,
        *,
        detector: LanguageDetector | None = None,
        translator: Translator | None = None,
        cache: TranslationCache | None = None,
    ) -> None:
        self.settings = settings or language_config
        self._detector = detector or get_language_detector(self.settings)
        self._translator = translator or get_translator(self.settings)
        self._cache = cache or get_translation_cache(self.settings.cache)

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def pipeline_language(self) -> str:
        return normalize_language_code(self.settings.pipeline_language)

    # ------------------------------------------------------------------
    # Inbound: user text -> English pipeline text
    # ------------------------------------------------------------------

    def to_pipeline_language(
        self,
        text: str,
        *,
        declared_language: str | None = None,
    ) -> InboundResult:
        """Detect/confirm the language of *text* and translate it to English.

        ``declared_language`` is an optional hint from the caller (e.g. the
        frontend's language selector). When it is a supported, normalizable
        code, it is trusted directly and Sarvam's LID call is skipped
        entirely -- the user's explicit selection outranks a heuristic
        guess. When it is absent or not supported, the text is run through
        :mod:`app.language.detector` instead.

        Never raises for translation-provider failures: those are the
        translator's concern (it degrades internally), so a gateway caller
        always gets a usable :class:`InboundResult` back.
        """
        pipeline_lang = self.pipeline_language

        if not self.settings.enabled:
            fallback_lang = normalize_language_code(self.settings.default_user_language)
            logger.debug(
                "language gateway disabled; passing inbound text through unchanged",
                extra={"event": "gateway_inbound_disabled_passthrough"},
            )
            return InboundResult(
                original_text=text,
                pipeline_text=text,
                source_language=fallback_lang,
                detection=self._disabled_detection(fallback_lang, reason="gateway_disabled"),
                was_translated=False,
                used_cache=False,
            )

        detection = self._resolve_source_language(text, declared_language)
        source_lang = detection.language_code

        if self.settings.skip_translation_for_english and detection.is_pipeline_language:
            logger.debug(
                "inbound text already in pipeline language; skipping translation",
                extra={
                    "event": "gateway_inbound_skip_english",
                    "source_lang": source_lang,
                },
            )
            return InboundResult(
                original_text=text,
                pipeline_text=text,
                source_language=source_lang,
                detection=detection,
                was_translated=False,
                used_cache=False,
            )

        cached = self._cache.get(source_lang, pipeline_lang, text)
        if cached is not None:
            logger.debug(
                "inbound translation cache hit",
                extra={
                    "event": "gateway_inbound_cache_hit",
                    "source_lang": source_lang,
                    "target_lang": pipeline_lang,
                },
            )
            return InboundResult(
                original_text=text,
                pipeline_text=cached,
                source_language=source_lang,
                detection=detection,
                was_translated=True,
                used_cache=True,
            )

        result = self._translator.translate(
            text,
            source_lang=source_lang,
            target_lang=pipeline_lang,
        )
        if result.used_fallback:
            # Don't cache untranslated text as a real translation, and don't
            # tell the caller a translation happened -- mirrors the same
            # guard in to_user_language().
            return InboundResult(
                original_text=text,
                pipeline_text=result.translated_text,
                source_language=source_lang,
                detection=detection,
                was_translated=False,
                used_cache=False,
                provider=result.provider,
                request_id=result.request_id,
            )
        self._cache.set(
            source_lang,
            pipeline_lang,
            text,
            result.translated_text,
            provider=result.provider,
            request_id=result.request_id,
        )
        logger.info(
            "translated inbound text to pipeline language",
            extra={
                "event": "gateway_inbound_translated",
                "source_lang": source_lang,
                "target_lang": pipeline_lang,
                "provider": result.provider,
                "request_id": result.request_id,
            },
        )
        return InboundResult(
            original_text=text,
            pipeline_text=result.translated_text,
            source_language=source_lang,
            detection=detection,
            was_translated=True,
            used_cache=False,
            provider=result.provider,
            request_id=result.request_id,
        )

    # ------------------------------------------------------------------
    # Outbound: English pipeline text -> user's language
    # ------------------------------------------------------------------

    def to_user_language(
        self,
        text: str,
        target_language: str,
    ) -> OutboundResult:
        """Localize English *text* into ``target_language`` for the user.

        Raises :class:`UnsupportedLanguageError` if ``target_language``
        does not normalize to a supported code -- unlike inbound detection,
        there is no heuristic fallback here: an outbound target is always
        an explicit value the caller chose (frontend selector or stored
        case preference), so a bad value is a caller bug worth surfacing
        rather than silently swallowing.
        """
        pipeline_lang = self.pipeline_language
        normalized_target = normalize_language_code(target_language)

        if not is_supported_language(normalized_target) and not is_pipeline_language(
            normalized_target
        ):
            raise UnsupportedLanguageError(
                f"Unsupported target language: {target_language!r}. "
                f"Expected one of: {sorted(SUPPORTED_LANGUAGE_CODES)}"
            )

        if not self.settings.enabled:
            logger.debug(
                "language gateway disabled; passing outbound text through unchanged",
                extra={"event": "gateway_outbound_disabled_passthrough"},
            )
            return OutboundResult(
                pipeline_text=text,
                localized_text=text,
                target_language=normalized_target,
                was_translated=False,
                used_cache=False,
            )

        if self.settings.skip_translation_for_english and is_pipeline_language(
            normalized_target
        ):
            return OutboundResult(
                pipeline_text=text,
                localized_text=text,
                target_language=normalized_target,
                was_translated=False,
                used_cache=False,
            )

        cached = self._cache.get(pipeline_lang, normalized_target, text)
        if cached is not None:
            logger.debug(
                "outbound translation cache hit",
                extra={
                    "event": "gateway_outbound_cache_hit",
                    "source_lang": pipeline_lang,
                    "target_lang": normalized_target,
                },
            )
            return OutboundResult(
                pipeline_text=text,
                localized_text=cached,
                target_language=normalized_target,
                was_translated=True,
                used_cache=True,
            )

        result = self._translator.translate(
            text,
            source_lang=pipeline_lang,
            target_lang=normalized_target,
        )
        if result.used_fallback:
            # translator.py already logged why (API error, no key, etc.) --
            # don't cache untranslated text as if it were a real translation,
            # and don't tell the caller a translation happened.
            return OutboundResult(
                pipeline_text=text,
                localized_text=result.translated_text,
                target_language=normalized_target,
                was_translated=False,
                used_cache=False,
                provider=result.provider,
                request_id=result.request_id,
            )
        self._cache.set(
            pipeline_lang,
            normalized_target,
            text,
            result.translated_text,
            provider=result.provider,
            request_id=result.request_id,
        )
        logger.info(
            "translated outbound text to user language",
            extra={
                "event": "gateway_outbound_translated",
                "source_lang": pipeline_lang,
                "target_lang": normalized_target,
                "provider": result.provider,
                "request_id": result.request_id,
            },
        )
        return OutboundResult(
            pipeline_text=text,
            localized_text=result.translated_text,
            target_language=normalized_target,
            was_translated=True,
            used_cache=False,
            provider=result.provider,
            request_id=result.request_id,
        )

    def localize_many(
        self,
        texts: dict[str, str],
        target_language: str,
    ) -> dict[str, OutboundResult]:
        """Localize a batch of named English strings (e.g. multiple fields
        of a resolution document) into ``target_language`` in one call.

        A thin convenience wrapper -- each entry still goes through the
        normal cache-then-translate path in ``to_user_language``, so
        repeated fields across cases/users still benefit from the cache.
        Falls back per-key rather than failing the whole batch: this is a
        UI-facing bulk call, and one field's translation issue should not
        blank out every other field the user would otherwise see.
        """
        results: dict[str, OutboundResult] = {}
        for key, value in texts.items():
            try:
                results[key] = self.to_user_language(value, target_language)
            except UnsupportedLanguageError:
                raise
            except Exception as exc:  # translator/cache surprises stay scoped to this key
                logger.warning(
                    "localize_many: failed to translate field; passing through",
                    extra={
                        "event": "gateway_localize_many_field_error",
                        "field": key,
                        "error": str(exc),
                    },
                )
                results[key] = OutboundResult(
                    pipeline_text=value,
                    localized_text=value,
                    target_language=normalize_language_code(target_language),
                    was_translated=False,
                    used_cache=False,
                )
        return results

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _resolve_source_language(
        self,
        text: str,
        declared_language: str | None,
    ) -> DetectionResult:
        """Prefer an explicit, supported ``declared_language`` over LID."""
        if declared_language:
            normalized = normalize_language_code(declared_language)
            if is_supported_language(normalized) or is_pipeline_language(normalized):
                logger.debug(
                    "using declared language; skipping detection call",
                    extra={
                        "event": "gateway_declared_language_used",
                        "declared_language": normalized,
                    },
                )
                return DetectionResult(
                    language_code=normalized,
                    script_code=None,
                    confidence=1.0,
                    is_confident=True,
                    is_supported=is_supported_language(normalized),
                    is_pipeline_language=is_pipeline_language(normalized),
                    used_fallback=False,
                    fallback_reason=None,
                    request_id=None,
                )
            logger.info(
                "declared language not supported; falling back to detection",
                extra={
                    "event": "gateway_declared_language_unsupported",
                    "declared_language": normalized,
                },
            )

        return self._detector.detect(text)

    @staticmethod
    def _disabled_detection(fallback_lang: str, *, reason: str) -> DetectionResult:
        """Build a fallback DetectionResult without invoking the detector,
        for use when the gateway itself is turned off.
        """
        return DetectionResult(
            language_code=fallback_lang,
            script_code=None,
            confidence=0.0,
            is_confident=False,
            is_supported=is_supported_language(fallback_lang),
            is_pipeline_language=is_pipeline_language(fallback_lang),
            used_fallback=True,
            fallback_reason=reason,
            request_id=None,
        )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Summarize gateway configuration + cache stats for a health/status
        endpoint, mirroring the shape of ``SarvamProvider.status()``.
        """
        return {
            "enabled": self.settings.enabled,
            "passthrough_when_disabled": self.settings.passthrough_when_disabled,
            "pipeline_language": self.pipeline_language,
            "default_user_language": normalize_language_code(
                self.settings.default_user_language
            ),
            "auto_detect": self.settings.auto_detect,
            "skip_translation_for_english": self.settings.skip_translation_for_english,
            "supported_languages": sorted(SUPPORTED_LANGUAGE_CODES),
            "cache": self._cache.stats.to_dict(),
        }


_default_gateway: LanguageGateway | None = None
_default_gateway_lock = threading.Lock()


def get_language_gateway(settings: LanguageConfig | None = None) -> LanguageGateway:
    """Return the process-wide language gateway singleton."""
    global _default_gateway
    if settings is not None:
        return LanguageGateway(settings)

    if _default_gateway is None:
        with _default_gateway_lock:
            if _default_gateway is None:
                _default_gateway = LanguageGateway()
    return _default_gateway


def to_pipeline_language(text: str, *, declared_language: str | None = None) -> InboundResult:
    """Convenience wrapper around the singleton gateway's inbound path."""
    return get_language_gateway().to_pipeline_language(
        text, declared_language=declared_language
    )


def to_user_language(text: str, target_language: str) -> OutboundResult:
    """Convenience wrapper around the singleton gateway's outbound path."""
    return get_language_gateway().to_user_language(text, target_language)