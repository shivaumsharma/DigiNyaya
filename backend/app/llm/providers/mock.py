"""
Mock LLM provider.

Used when no real provider (Sarvam/Ollama) is available.
Allows the application to boot and supports testing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional

from app.llm.base import BaseLLMProvider


class MockProvider(BaseLLMProvider):
    """
    Stub implementation of BaseLLMProvider used when no real provider
    (Sarvam/Ollama) is configured or reachable.

    generate()/generate_json()/generate_stream() deliberately return
    falsy values (never placeholder text) so every call site's existing
    `if out:` / `if data:` / `acc or None` fallback-to-scripted check
    engages honestly instead of mistaking mock output for a genuine LLM
    response and labelling it engine="llm" in a resolution document.
    is_available()/status() report unavailable for the same reason: to
    this codebase "available" means "a real LLM is behind this", which a
    mock stub never is -- otherwise the frontend's engine badge shows
    "Live LLM · mock" when nothing live is actually configured.
    """

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

        return ""

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

        return None

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

        return
        yield  # pragma: no cover -- makes this a generator function

    def embed(self, text: str) -> List[float]:
        """
        Return a deterministic fake embedding.
        """

        dimension = 768

        return [0.0] * dimension

    def status(self) -> Dict[str, Any]:

        return {
            "provider": "mock",
            "engine": "MockProvider",
            "online": False,
            "available": False,
            "model": "mock",
            "models": ["mock"],
        }

    def is_available(self) -> bool:
        return False