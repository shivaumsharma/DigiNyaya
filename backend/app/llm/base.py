"""
Abstract interface for all LLM providers.

Every provider (Ollama, Sarvam, OpenAI, Anthropic, etc.)
must implement this contract so the rest of DigiNyaya
remains provider-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional


class BaseLLMProvider(ABC):
    """
    Base interface for every language model provider.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """
        Generate a text response.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate structured JSON output. Returns None (not a raised exception)
        if the underlying model didn't return valid JSON, so callers can fall
        back to scripted behaviour instead of crashing the pipeline.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """
        Stream generated text incrementally.
        """
        raise NotImplementedError

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """
        Generate a single embedding vector for one string.

        NOTE: this is single-item by design. Batch callers must go through
        app.llm.client.embed(), which loops over a list on top of this.
        Never pass a list[str] directly to a provider's embed().
        """
        raise NotImplementedError

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """
        Return provider diagnostics.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True if provider is reachable.
        """
        raise NotImplementedError

    def last_usage(self) -> Optional[Dict[str, int]]:
        """
        Token usage from the most recent generate()/generate_json() call, as
        {"prompt_tokens", "completion_tokens", "total_tokens"} if the
        provider's API exposes it, else None. Concrete (not abstract) with a
        safe default so providers that don't track usage (Mock, and Ollama
        until wired up) don't need to implement anything -- only
        SarvamProvider currently overrides this, by setting self._last_usage
        in _chat().
        """
        return getattr(self, "_last_usage", None)