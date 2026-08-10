"""
OpenAI provider.

Panel-only today (see scripts/panel_review.py) -- never instantiated by the
live pipeline (app.llm.factory's normal provider selection has no "openai"
branch). Built on the same BaseLLMProvider contract as Sarvam/Ollama so it
could become a first-class provider later without a redesign, but that's not
what this exists for right now: it's a second, independent voice for
cross-checking Agent 4/5 outputs against Sarvam's, not a replacement.

Uses OpenAI's Chat Completions API directly via `requests` (no `openai` SDK
dependency added -- matches this codebase's existing convention of raw HTTP
for LLM providers, see sarvam.py).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Generator, List

import requests

from app.llm.base import BaseLLMProvider
from app.llm.config import config


class OpenAIProvider(BaseLLMProvider):
    def __init__(self):
        self.base_url = config.openai_base_url.rstrip("/")
        self.api_key = config.openai_api_key
        self.model = config.openai_model
        self.timeout = config.timeout
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self._session = requests.Session()

    def _chat(self, messages, *, temperature, model=None, max_tokens=None, response_format=None, stream=False):
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format

        response = self._session.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
            stream=stream,
        )
        response.raise_for_status()
        if not stream:
            try:
                self._last_usage = response.json().get("usage")
            except (ValueError, AttributeError):
                self._last_usage = None
        return response

    def generate(self, prompt, *, system=None, temperature=0.2, model=None, max_tokens=None, reasoning_effort=None):
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        response = self._chat(messages, temperature=temperature, model=model, max_tokens=max_tokens)
        return response.json()["choices"][0]["message"]["content"]

    def generate_json(self, prompt, *, system=None, schema=None, temperature=0.0, model=None, max_tokens=None, reasoning_effort=None):
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        response = self._chat(
            messages, temperature=temperature, model=model, max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            return None

    def generate_stream(self, prompt, *, system=None, temperature=0.2, model=None, max_tokens=None, reasoning_effort=None) -> Generator[str, None, None]:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        response = self._chat(messages, temperature=temperature, model=model, max_tokens=max_tokens, stream=True)
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode()
            if not line.startswith("data:"):
                continue
            line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                payload = json.loads(line)
                delta = payload["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta
            except Exception:
                continue

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError(
            "OpenAIProvider.embed() isn't wired up -- this provider is panel-only "
            "(scripts/panel_review.py), which never needs embeddings; the app's real "
            "embedding path is app.llm.factory.get_embedding_provider() (Ollama)."
        )

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            response = self._session.get(f"{self.base_url}/models", headers=self.headers, timeout=5)
            return response.ok
        except requests.RequestException:
            return False

    def status(self) -> Dict[str, Any]:
        available = self.is_available()
        return {
            "provider": "openai",
            "engine": self.__class__.__name__,
            "online": available,
            "available": available,
            "model": self.model,
            "models": [self.model],
        }
