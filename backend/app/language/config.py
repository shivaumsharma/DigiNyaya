"""Central configuration for the DigiNyaya Language Gateway.

This module is the single source of truth for:
- Feature toggles (gateway on/off, backward-compatible passthrough)
- Supported user languages and the internal pipeline language (English)
- Sarvam translation / language-ID endpoints (reuses SARVAM_API_KEY)
- Translation cache settings
- Detection, chunking, timeout, and structured-logging defaults

The multi-agent pipeline (Ingestion → Research → Analysis → Mediation →
Resolution) always runs in ``pipeline_language``. The gateway translates
user input to that language before the pipeline and back afterward.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

_DEFAULT_CACHE_DIR = _BACKEND_ROOT / "data_cache" / "translations"

# Sarvam Mayura v1 — the 11 languages exposed in the frontend selector.
SUPPORTED_LANGUAGES: tuple[dict[str, str], ...] = (
    {"code": "en-IN", "label": "English", "native": "English"},
    {"code": "hi-IN", "label": "Hindi", "native": "हिन्दी"},
    {"code": "ta-IN", "label": "Tamil", "native": "தமிழ்"},
    {"code": "te-IN", "label": "Telugu", "native": "తెలుగు"},
    {"code": "kn-IN", "label": "Kannada", "native": "ಕನ್ನಡ"},
    {"code": "ml-IN", "label": "Malayalam", "native": "മലയാളം"},
    {"code": "mr-IN", "label": "Marathi", "native": "मराठी"},
    {"code": "bn-IN", "label": "Bengali", "native": "বাংলা"},
    {"code": "gu-IN", "label": "Gujarati", "native": "ગુજરાતી"},
    {"code": "pa-IN", "label": "Punjabi", "native": "ਪੰਜਾਬੀ"},
    {"code": "od-IN", "label": "Odia", "native": "ଓଡ଼ିଆ"},
)

SUPPORTED_LANGUAGE_CODES: frozenset[str] = frozenset(
    lang["code"] for lang in SUPPORTED_LANGUAGES
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def normalize_language_code(code: str) -> str:
    """Normalize a BCP-47 tag to the form used across the gateway."""
    return code.strip().replace("_", "-")


def is_pipeline_language(code: str) -> bool:
    """Return True when *code* is the internal English pipeline language."""
    normalized = normalize_language_code(code).lower()
    return normalized in {"en-in", "en"}


def is_supported_language(code: str) -> bool:
    """Return True when *code* is a gateway-supported user language."""
    return normalize_language_code(code) in SUPPORTED_LANGUAGE_CODES


@dataclass(frozen=True)
class CacheSettings:
    """Runtime settings for the translation cache (see ``app.language.cache``)."""

    enabled: bool = _env_bool("LANGUAGE_CACHE_ENABLED", True)
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("LANGUAGE_CACHE_DIR", str(_DEFAULT_CACHE_DIR))
        )
    )
    ttl_seconds: int = _env_int("LANGUAGE_CACHE_TTL_SECONDS", 86_400)
    max_memory_entries: int = _env_int("LANGUAGE_CACHE_MAX_MEMORY", 512)


@dataclass(frozen=True)
class LanguageConfig:
    """Immutable configuration for the Language Gateway."""

    # ------------------------------------------------------------------
    # Feature toggle
    # ------------------------------------------------------------------

    enabled: bool = _env_bool("LANGUAGE_GATEWAY_ENABLED", True)

    # When disabled, pass text through unchanged (backward compatibility).
    passthrough_when_disabled: bool = _env_bool(
        "LANGUAGE_PASSTHROUGH_WHEN_DISABLED",
        True,
    )

    # ------------------------------------------------------------------
    # Languages
    # ------------------------------------------------------------------

    # Internal pipeline language — agents always operate in English.
    pipeline_language: str = os.getenv("LANGUAGE_PIPELINE", "en-IN")

    # Default when the client does not send a preference.
    default_user_language: str = os.getenv("LANGUAGE_DEFAULT", "en-IN")

    # Skip Sarvam calls when input is already in the pipeline language.
    skip_translation_for_english: bool = _env_bool(
        "LANGUAGE_SKIP_ENGLISH",
        True,
    )

    # ------------------------------------------------------------------
    # Sarvam translation / detection (reuses LLM credentials)
    # ------------------------------------------------------------------

    sarvam_api_key: str | None = os.getenv("SARVAM_API_KEY")

    # Chat completions use /v1; translation and LID live on the root host.
    sarvam_translate_url: str = os.getenv(
        "SARVAM_TRANSLATE_URL",
        "https://api.sarvam.ai/translate",
    )

    sarvam_lid_url: str = os.getenv(
        "SARVAM_LID_URL",
        "https://api.sarvam.ai/text-lid",
    )

    translation_model: str = os.getenv(
        "SARVAM_TRANSLATION_MODEL",
        "mayura:v1",
    )

    # Sarvam Mayura hard limit; longer text is chunked by the translator.
    max_chunk_chars: int = _env_int("LANGUAGE_MAX_CHUNK_CHARS", 1_000)

    timeout: int = _env_int("LANGUAGE_TIMEOUT", 30)

    retry_attempts: int = _env_int("LANGUAGE_RETRY_ATTEMPTS", 2)

    retry_delay: float = _env_float("LANGUAGE_RETRY_DELAY", 1.0)

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    auto_detect: bool = _env_bool("LANGUAGE_AUTO_DETECT", True)

    detection_confidence_threshold: float = _env_float(
        "LANGUAGE_DETECTION_CONFIDENCE",
        0.70,
    )

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    cache: CacheSettings = field(default_factory=CacheSettings)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    enable_structured_logging: bool = _env_bool(
        "LANGUAGE_ENABLE_LOGGING",
        True,
    )

    log_level: str = os.getenv("LANGUAGE_LOG_LEVEL", "INFO").upper()


config = LanguageConfig()
