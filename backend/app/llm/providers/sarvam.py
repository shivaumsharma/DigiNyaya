"""
Sarvam provider.

Supports:
- Chat Completion
- Structured JSON
- Streaming
- Embeddings

Implements BaseLLMProvider.
"""

from __future__ import annotations
import json
import requests
from typing import Any, Dict, List, Optional
from app.llm.base import BaseLLMProvider
from app.llm.config import config


class SarvamProvider(BaseLLMProvider):

    MODEL_MAP = {
        "classification": config.sarvam_fast_model,
        "retrieval": config.sarvam_fast_model,
        "analysis": config.sarvam_reasoning_model,
        "mediation": config.sarvam_reasoning_model,
        "drafting": config.sarvam_reasoning_model,
    }

    def __init__(self):

        self.base_url = config.sarvam_base_url.rstrip("/")

        self.api_key = config.sarvam_api_key

        self.timeout = config.timeout

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # One 5-agent case run makes ~4 calls to Sarvam; module-level
        # requests.post()/get() opens a brand-new TCP+TLS connection every
        # single time. A shared Session reuses the underlying connection
        # (HTTP keep-alive) across calls from this provider instance instead.
        self._session = requests.Session()

    # --------------------------------------------------------

    def _resolve_model(
        self,
        model: Optional[str],
    ) -> str:

        if model is None:
            return self.MODEL_MAP["analysis"]

        return self.MODEL_MAP.get(
            model,
            model,
        )
    

    def _chat(
        self,
        messages,
        *,
        temperature,
        model=None,
        stream=False,
        response_format=None,
        max_tokens=None,
        reasoning_effort=None,
    ):

        payload = {
            "model": self._resolve_model(model),
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Sarvam's reasoning models (sarvam-105b) default to "medium" reasoning
        # effort whenever this field is *omitted* from the request, emitting a
        # verbose reasoning_content trace that can consume the entire
        # max_tokens budget before any answer is written to `content`
        # (content: null, finish_reason: "length"). Empirically, "low" is NOT
        # reliably low for schema-constrained JSON prompts -- it still burned
        # 350+ reasoning tokens and starved a 600-token budget in testing.
        # Explicitly sending reasoning_effort: null (Python None) is what
        # actually disables reasoning -- confirmed empirically to produce
        # correct content in ~50 completion tokens with reasoning_content
        # empty. So this field is always sent (never omitted): None -> JSON
        # null -> disabled; "low"/"medium"/"high" pass through for callers
        # that explicitly want deeper reasoning.
        payload["reasoning_effort"] = reasoning_effort

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

        # Non-streaming responses carry a usage block (OpenAI-compatible
        # shape); a streaming response's body can't be read here without
        # consuming the generator the caller still needs, so usage is only
        # captured for the two non-streaming call sites (generate,
        # generate_json) -- streaming callers (generate_stream) don't get a
        # last_usage() reading, which is fine, cost measurement never uses it.
        if not stream:
            try:
                self._last_usage = response.json().get("usage")
            except (ValueError, AttributeError):
                self._last_usage = None

        return response
    
    def generate(
        self,
        prompt,
        *,
        system=None,
        temperature=0.2,
        model=None,
        max_tokens=None,
        reasoning_effort=None,
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
            reasoning_effort=reasoning_effort,
        )

        return response.json()["choices"][0]["message"]["content"]

    def generate_json(
        self,
        prompt,
        *,
        system=None,
        schema=None,
        temperature=0.0,
        model=None,
        max_tokens=None,
        reasoning_effort=None,
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
            reasoning_effort=reasoning_effort,
            response_format={
                "type": "json_object"
            },
        )

        try:
            return json.loads(
                response.json()["choices"][0]["message"]["content"]
            )
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            return None
    def is_available(self) -> bool:

      if not self.api_key:
          return False

      try:

          response = self._session.get(
              f"{self.base_url}/models",
              headers=self.headers,
              timeout=5,
          )

          return response.ok

      except requests.RequestException:

          return False
        
    def status(self) -> Dict[str, Any]:
      """
      Return provider diagnostics.
      """

      available = self.is_available()
      return {
          "provider": "sarvam",
          "engine": self.__class__.__name__,
          "online": available,
          "available": available,
          "model": config.sarvam_fast_model,
          "models": [
              config.sarvam_fast_model,
              config.sarvam_reasoning_model,
          ],
      }
    
    def embed(self,text: str,)->List[float]:
      """
      Sarvam does not currently expose a public text-embeddings endpoint.
      Their documented API surface is chat completion, translation, STT/TTS,
      transliteration and language-ID -- there is no /embeddings route, and
      "sarvam-embed" is not a real model. Calling this used to POST to a
      nonexistent endpoint and get back a 404.

      This method should never be reached in normal operation: embeddings
      are resolved via app.llm.factory.get_embedding_provider() (Ollama),
      never via the active chat provider. This raises explicitly so any
      future direct call fails loudly with a clear reason instead of a
      confusing 404 from Sarvam's servers.
      """
      raise NotImplementedError(
          "SarvamProvider has no embeddings endpoint -- Sarvam's public API "
          "does not offer text embeddings. Use app.llm.client.embed(), which "
          "routes to Ollama's nomic-embed-text via get_embedding_provider(), "
          "not through the active chat provider."
      )
    
    def generate_stream(
    self,
    prompt,
    *,
    system=None,
    temperature=0.2,
    model=None,
    max_tokens=None,
    reasoning_effort=None,
):
      """
      Stream generated output.
      """

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
          reasoning_effort=reasoning_effort,
          stream=True,
      )

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

              delta = (
                  payload["choices"][0]
                  .get("delta", {})
                  .get("content")
              )

              if delta:
                  yield delta

          except Exception:

              continue