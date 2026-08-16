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

    # Fast / inexpensive reasoning. sarvam-30b was deprecated by Sarvam and
    # is now hard-rejected by the API (confirmed live: a 400 naming
    # sarvam-105b/sarvam-105b-conversations as the only valid replacements) --
    # every classification/retrieval call using the old default was silently
    # failing and falling back to scripted logic. sarvam-105b-conversations
    # is the lighter/faster variant of the current flagship, keeping the
    # original fast-vs-reasoning cost tiering intent (sarvam_reasoning_model
    # below still points at full sarvam-105b for analysis/mediation/drafting).
    sarvam_fast_model: str = os.getenv(
        "SARVAM_FAST_MODEL",
        "sarvam-105b-conversations"
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
    # OpenAI -- only used by scripts/panel_review.py's multi-model panel
    # today, never by the live pipeline (app.llm.factory only instantiates
    # this when explicitly asked -- see get_panel_provider()). No key set =
    # simply unavailable, same graceful-degradation convention as everywhere
    # else in this module.
    # ------------------------------------------------------------------

    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    # ------------------------------------------------------------------
    # Anthropic -- same "panel-only, never the live pipeline" scope as OpenAI above.
    # ------------------------------------------------------------------

    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    anthropic_base_url: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

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

    # A single embedding call is a small, fast request when Ollama is
    # actually reachable (a cold-start embed of one string measured ~3-4s in
    # practice) -- it should never need anywhere close to the 120s
    # `timeout` above, which exists for slow multi-step chat/reasoning
    # generation. Using the general `timeout` for embed() meant a single
    # unreachable/overloaded Ollama instance could block each embed call
    # (the corpus embed on first use, plus one per subsequent retrieval
    # query) for up to 120s before app.rag.index's existing try/except
    # fallback to keyword search could even engage -- confirmed directly:
    # a pipeline run hung 2+ minutes on this alone. Kept short and
    # separately configurable so a slow-but-genuinely-working embed model
    # isn't starved of the (much longer) budget chat completions still get.
    embed_timeout: int = int(
        os.getenv(
            "LLM_EMBED_TIMEOUT",
            "10"
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