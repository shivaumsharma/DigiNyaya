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
    Deterministic mock implementation of BaseLLMProvider.
    """

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:

        return (
            "[MOCK PROVIDER]\n\n"
            "No LLM provider is currently configured.\n\n"
            f"Model: {model or 'default'}\n"
            f"Prompt Length: {len(prompt)} characters"
        )

    def generate_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:

        return {
            "provider": "mock",
            "success": True,
            "message": "Mock JSON response.",
            "model": model or "mock",
            "prompt_length": len(prompt),
        }

    def generate_stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Generator[str, None, None]:

        text = self.generate(
            prompt,
            system=system,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
        )

        for word in text.split():
            yield word + " "

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
            "online": True,
            "available": True,
            "model": "mock",
            "models": ["mock"],
        }

    def is_available(self) -> bool:
        return True