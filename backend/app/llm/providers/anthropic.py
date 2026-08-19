"""
Anthropic provider.

Panel-only today (see scripts/panel_review.py) -- same scope note as
app/llm/providers/openai.py: a second/third independent voice for
cross-checking Agent 4/5 outputs, never instantiated by the live pipeline.

Anthropic's Messages API has a different shape than the OpenAI-compatible
one Sarvam/OpenAI share here: `system` is a top-level field (not a message),
`max_tokens` is REQUIRED (not optional), the response body is
`content: [{"type": "text", "text": ...}]` rather than
`choices[0].message.content`, and there's no native JSON response_format
mode -- generate_json() asks for JSON in the prompt and parses leniently
(stripping a ```json fence if the model adds one anyway).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Generator, List

import requests

from app.llm.base import BaseLLMProvider
from app.llm.config import config

_ANTHROPIC_VERSION = "2023-06-01"
# Anthropic requires max_tokens on every request -- this codebase's other
# providers treat it as optional (None = provider/model default). Only used
# here when a caller genuinely passes None.
_DEFAULT_MAX_TOKENS = 1024

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AnthropicProvider(BaseLLMProvider):
    def __init__(self):
        self.base_url = config.anthropic_base_url.rstrip("/")
        self.api_key = config.anthropic_api_key
        self.model = config.anthropic_model
        self.timeout = config.timeout
        self.headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        self._session = requests.Session()

    def _messages(self, prompt: str, *, system, temperature, model=None, max_tokens=None, stream=False):
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens or _DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if system:
            payload["system"] = system

        response = self._session.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
            stream=stream,
        )
        response.raise_for_status()
        if not stream:
            try:
                usage = response.json().get("usage") or {}
                self._last_usage = {
                    "prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"),
                    "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
                }
            except (ValueError, AttributeError):
                self._last_usage = None
        return response

    def _extract_text(self, response) -> str:
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def generate(self, prompt, *, system=None, temperature=0.2, model=None, max_tokens=None, reasoning_effort=None):
        response = self._messages(prompt, system=system, temperature=temperature, model=model, max_tokens=max_tokens)
        return self._extract_text(response)

    def generate_json(self, prompt, *, system=None, schema=None, temperature=0.0, model=None, max_tokens=None, reasoning_effort=None):
        # No native JSON mode -- reinforce the instruction and parse
        # defensively (strip a markdown fence if the model wraps its answer
        # in one anyway, which Claude models do fairly often despite being told not to).
        json_prompt = prompt + "\n\nRespond with ONLY the raw JSON object, no markdown fences, no commentary."
        response = self._messages(json_prompt, system=system, temperature=temperature, model=model, max_tokens=max_tokens)
        text = _JSON_FENCE_RE.sub("", self._extract_text(response)).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None

    def generate_stream(self, prompt, *, system=None, temperature=0.2, model=None, max_tokens=None, reasoning_effort=None) -> Generator[str, None, None]:
        response = self._messages(prompt, system=system, temperature=temperature, model=model, max_tokens=max_tokens, stream=True)
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode()
            if not line.startswith("data:"):
                continue
            line = line[5:].strip()
            try:
                payload = json.loads(line)
                if payload.get("type") == "content_block_delta":
                    delta = payload.get("delta", {}).get("text")
                    if delta:
                        yield delta
            except Exception:
                continue

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError(
            "AnthropicProvider.embed() isn't wired up -- Anthropic has no public "
            "embeddings endpoint, and this provider is panel-only anyway (see "
            "scripts/panel_review.py); the app's real embedding path is "
            "app.llm.factory.get_embedding_provider() (Ollama)."
        )

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            # Anthropic has no lightweight /models health-check endpoint the
            # way Sarvam/OpenAI do -- a minimal real message call is the
            # standard way to check the key/connection actually works.
            response = self._session.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json={"model": self.model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                timeout=5,
            )
            return response.ok
        except requests.RequestException:
            return False

    def status(self) -> Dict[str, Any]:
        available = self.is_available()
        return {
            "provider": "anthropic",
            "engine": self.__class__.__name__,
            "online": available,
            "available": available,
            "model": self.model,
            "models": [self.model],
        }
