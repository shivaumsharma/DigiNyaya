"""
LLM Provider Factory.

Responsible for selecting and instantiating the appropriate
LLM provider.

Selection order:

1. Explicit provider from config
2. Auto-detect (Sarvam -> Ollama -> Mock)
"""

from __future__ import annotations
from app.llm.base import BaseLLMProvider
from app.llm.config import config
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.sarvam import SarvamProvider
from app.llm.providers.mock import MockProvider


class LLMFactory:

    @staticmethod
    def create() -> BaseLLMProvider:

        provider = config.provider.lower()
        print("=" * 60)
        print("Provider :", config.provider)
        print("Host     :", config.ollama_host)
        print("Model    :", config.ollama_chat_model)
        print("=" * 60)
        # -----------------------------
        # Explicit Provider Selection
        # -----------------------------

        if provider == "sarvam":

            try:
                instance = SarvamProvider()

                if not instance.is_available():
                    raise RuntimeError("Sarvam provider unavailable.")

                return instance

            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize Sarvam provider: {e}"
                )

        elif provider == "ollama":

            try:
                instance = OllamaProvider()

                if not instance.is_available():
                    raise RuntimeError("Ollama provider unavailable.")

                return instance

            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize Ollama provider: {e}"
                )

        elif provider == "mock":

            return MockProvider()

        # -----------------------------
        # Auto Selection
        # -----------------------------

        elif provider == "auto":

            try:
                sarvam = SarvamProvider()

                if sarvam.is_available():
                    return sarvam

            except Exception:
                pass

            try:
                ollama = OllamaProvider()

                if ollama.is_available():
                    return ollama

            except Exception:
                pass

            return MockProvider()

        raise ValueError(
            f"Unknown provider '{provider}'"
        )


_provider: BaseLLMProvider | None = None


def get_provider() -> BaseLLMProvider:
    """
    Return a singleton provider instance for chat/generation.
    """

    global _provider

    if _provider is None:
        _provider = LLMFactory.create()

    return _provider


def reload_provider() -> BaseLLMProvider:
    """
    Recreate provider instance.
    """

    global _provider

    _provider = LLMFactory.create()

    return _provider


# ---------------------------------------------------------------------
# Embedding provider — resolved SEPARATELY from the chat provider.
#
# Sarvam does not currently expose a public text-embeddings endpoint (its
# API surface is chat completion, translation, STT/TTS, transliteration and
# language-ID only — no /embeddings route exists on their platform). So
# "whichever provider is handling chat also handles embeddings" is a false
# assumption: if DIGINYAYA_LLM_PROVIDER=sarvam, embeddings must NOT also
# route to Sarvam, or every call 404s.
#
# Embeddings are always sourced from Ollama's local nomic-embed-text model,
# regardless of which provider is doing chat/reasoning. If Ollama isn't
# reachable, this returns None and rag/index.py's existing keyword-search
# fallback engages honestly (method="keyword") instead of crashing.
# ---------------------------------------------------------------------
_embedding_provider: BaseLLMProvider | None = None
_embedding_provider_checked = False


def get_embedding_provider() -> BaseLLMProvider | None:
    global _embedding_provider, _embedding_provider_checked

    if _embedding_provider_checked:
        return _embedding_provider

    _embedding_provider_checked = True
    try:
        candidate = OllamaProvider()
        if candidate.is_available():
            _embedding_provider = candidate
    except Exception:
        _embedding_provider = None

    return _embedding_provider


def reload_embedding_provider() -> BaseLLMProvider | None:
    global _embedding_provider, _embedding_provider_checked
    _embedding_provider_checked = False
    return get_embedding_provider()