"""
Ollama provider implementation.

Uses local Ollama for:
- Chat completion
- JSON generation
- Streaming
- Embeddings

Implements BaseLLMProvider.
"""

from __future__ import annotations

import json
import requests

from typing import Any, Dict, Generator, List, Optional

from app.llm.base import BaseLLMProvider
from app.llm.config import config


class OllamaProvider(BaseLLMProvider):

    def __init__(self):

        self.base_url = config.ollama_host.rstrip("/")

        self.chat_model = config.ollama_chat_model

        self.embedding_model = config.ollama_embedding_model

        self.timeout = config.timeout

        self.embed_timeout = config.embed_timeout

    # ---------------------------------------------------------

    def _chat(
        self,
        messages,
        *,
        temperature,
        model=None,
        stream=False,
        format=None,
        max_tokens=None,
    ):

        payload = {
            "model": model or self.chat_model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens is not None:
            # Ollama's equivalent of max_tokens is num_predict.
            payload["options"]["num_predict"] = max_tokens

        if format is not None:
            payload["format"] = format

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
            stream=stream,
        )

        response.raise_for_status()

        return response

    # ---------------------------------------------------------

    def generate(
        self,
        prompt,
        *,
        system=None,
        temperature=0.2,
        model=None,
        max_tokens=None,
    ):

        messages = []

        if system:
            messages.append({
                "role": "system",
                "content": system,
            })

        messages.append({
            "role": "user",
            "content": prompt,
        })

        response = self._chat(
            messages,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
        )

        return response.json()["message"]["content"]

    # ---------------------------------------------------------

    def generate_json(
        self,
        prompt,
        *,
        system=None,
        schema=None,
        temperature=0.0,
        model=None,
        max_tokens=None,
    ):

        messages = []

        if system:
            messages.append({
                "role": "system",
                "content": system,
            })

        messages.append({
            "role": "user",
            "content": prompt,
        })

        response = self._chat(
            messages,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
            format="json",  # ask Ollama to constrain output to valid JSON
        )

        text = response.json()["message"]["content"]

        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            # Model didn't return parseable JSON -- let the caller fall back
            # to scripted behaviour instead of crashing the pipeline.
            return None

    # ---------------------------------------------------------

    def generate_stream(
        self,
        prompt,
        *,
        system=None,
        temperature=0.2,
        model=None,
        max_tokens=None,
    ) -> Generator[str, None, None]:

        messages = []

        if system:
            messages.append({
                "role": "system",
                "content": system,
            })

        messages.append({
            "role": "user",
            "content": prompt,
        })

        response = self._chat(
            messages,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
            stream=True,
        )

        for line in response.iter_lines():

            if not line:
                continue

            data = json.loads(line)

            if "message" in data:

                yield data["message"].get("content", "")

    # ---------------------------------------------------------

    def embed(
        self,
        text,
    ) -> List[float]:

        response = requests.post(
            f"{self.base_url}/api/embeddings",
            json={
                "model": self.embedding_model,
                "prompt": text,
            },
            timeout=self.embed_timeout,
        )

        response.raise_for_status()

        return response.json()["embedding"]

    # ---------------------------------------------------------

    def status(self):

        try:

            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5,
            )

            return {
                "provider": "ollama",
                "engine": self.__class__.__name__,
                "online": response.ok,
                "available": response.ok,
                "model": self.chat_model,
                "models": [m.get("name") for m in response.json().get("models", [])],
            }

        except Exception:

            return {
                "provider": "ollama",
                "engine": self.__class__.__name__,
                "online": False,
                "available": False,
                "model": self.chat_model,
                "models": [],
            }

    # ---------------------------------------------------------

    def is_available(self):

        try:

            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=2,
            )

            return response.ok

        except Exception:

            return False