"""
Central configuration for DigiNyaya's LLM layer.

This module is the single source of truth for:
- Provider selection
- API credentials
- Default models
- Timeouts
- Retry configuration
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

@dataclass(frozen=True)
class LLMConfig:
    """
    Immutable configuration for the inference layer.
    """

    # ------------------------------------------------------------------
    # Provider Selection
    # ------------------------------------------------------------------

    # auto | ollama | sarvam | mock
    provider: str = os.getenv(
        "DIGINYAYA_LLM_PROVIDER",
        "auto"
    ).lower()

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------

    ollama_host: str = os.getenv(
        "OLLAMA_HOST",
        "http://localhost:11434"
    )

    ollama_chat_model: str = os.getenv(
        "OLLAMA_CHAT_MODEL",
        "qwen2.5:7b-instruct"
    )

    ollama_embedding_model: str = os.getenv(
        "OLLAMA_EMBED_MODEL",
        "nomic-embed-text"
    )

    # ------------------------------------------------------------------
    # Sarvam
    # ------------------------------------------------------------------

    sarvam_api_key: str | None = os.getenv(
        "SARVAM_API_KEY"
    )

    sarvam_base_url: str = os.getenv(
        "SARVAM_BASE_URL",
        "https://api.sarvam.ai/v1"
    )

    # Fast / inexpensive reasoning
    sarvam_fast_model: str = os.getenv(
        "SARVAM_FAST_MODEL",
        "sarvam-30b"
    )

    # Highest-quality reasoning
    sarvam_reasoning_model: str = os.getenv(
        "SARVAM_REASONING_MODEL",
        "sarvam-105b"
    )

    # Embedding model
    sarvam_embedding_model: str = os.getenv(
        "SARVAM_EMBED_MODEL",
        "sarvam-embed"
    )

    # ------------------------------------------------------------------
    # Generation Defaults
    # ------------------------------------------------------------------

    default_temperature: float = float(
        os.getenv(
            "LLM_TEMPERATURE",
            "0.2"
        )
    )

    max_tokens:int=int(
        os.getenv(
            "LLM_MAX_TOKENS",
            "4096"
        )
    )

    timeout: int = int(
        os.getenv(
            "LLM_TIMEOUT",
            "120"
        )
    )

    # ------------------------------------------------------------------
    # Retry Behaviour
    # ------------------------------------------------------------------

    retry_attempts: int = int(
        os.getenv(
            "LLM_RETRY_ATTEMPTS",
            "2"
        )
    )

    retry_delay: float = float(
        os.getenv(
            "LLM_RETRY_DELAY",
            "1.5"
        )
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    enable_logging: bool = (
        os.getenv(
            "LLM_ENABLE_LOGGING",
            "true"
        ).lower()
        == "true"
    )


config = LLMConfig()