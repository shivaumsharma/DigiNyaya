"""Regression test: OllamaProvider.embed() must fail fast against an
unreachable host, not hang toward the general (120s) LLM_TIMEOUT budget
meant for slow chat/reasoning calls.

Directly observed bug: app.rag.index._ensure_embeddings()/retrieve() embed
the precedent corpus (and each query) via this method; using the general
`timeout` here meant a single unreachable/overloaded Ollama instance could
block a pipeline run for minutes even though both call sites already
try/except around the call and fall back to keyword search -- the fallback
just couldn't engage until the (very long) timeout actually expired.

Run with (from backend/):
    python -m unittest tests.test_ollama_embed_timeout -v
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.llm.providers.ollama as ollama_module  # noqa: E402
from app.llm.config import LLMConfig, config as real_config  # noqa: E402

# An address in the TEST-NET-1 documentation range (RFC 5737): guaranteed
# never to be a real, reachable host, so this test can't accidentally pass
# because *something* happens to answer on it.
_UNREACHABLE_HOST = "http://192.0.2.1:11434"


class TestOllamaEmbedTimeoutConfig(unittest.TestCase):
    def test_embed_timeout_is_configured_shorter_than_general_timeout(self):
        # A static check on the real, process-wide config: embed_timeout
        # must be the short knob, timeout the long one -- not the same
        # value (which would silently undo the fix).
        self.assertLess(real_config.embed_timeout, real_config.timeout)

    def test_provider_stores_embed_timeout_distinct_from_timeout(self):
        provider = ollama_module.OllamaProvider()
        self.assertEqual(provider.embed_timeout, real_config.embed_timeout)
        self.assertNotEqual(provider.embed_timeout, provider.timeout)


class TestOllamaEmbedFailsFastOnUnreachableHost(unittest.TestCase):
    def test_embed_raises_within_a_few_seconds_not_toward_120s(self):
        fake_config = LLMConfig(ollama_host=_UNREACHABLE_HOST, embed_timeout=2)
        with patch.object(ollama_module, "config", fake_config):
            provider = ollama_module.OllamaProvider()
            self.assertEqual(provider.embed_timeout, 2)

            start = time.time()
            with self.assertRaises(Exception):
                provider.embed("some corpus text to embed")
            elapsed = time.time() - start

        # Generous margin above the configured 2s timeout (connection
        # setup/DNS overhead), but nowhere near the 120s general timeout --
        # this is the assertion that would have caught the original bug.
        self.assertLess(elapsed, 15.0)


if __name__ == "__main__":
    unittest.main()
