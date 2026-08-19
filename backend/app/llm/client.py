"""
Public LLM client interface.

This module exposes a stable API for the rest of DigiNyaya.

Agents should ONLY import from this module and never interact
with providers directly.

Changing providers (Ollama, Sarvam, OpenAI, etc.) should never
require changes outside the llm package.

Call convention (matches every call site in app/agents and app/rag):

    llm.generate(prompt, system=llm.SYSTEM_PROMPT, max_tokens=500)
    llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=500)
    llm.generate_stream(prompt, system=llm.SYSTEM_PROMPT, max_tokens=600)
    llm.embed(["doc one", "doc two", ...])   # -> list[list[float]]
    llm.embed("single string")               # -> list[float]

`prompt` is always positional; `system` and everything else is keyword-only.
This used to not match what the agents called with (they passed system as a
second positional arg, plus a max_tokens kwarg the old signature didn't
accept at all) -- that mismatch is what crashed every LLM-touching agent
call with a TypeError. Fixed here + at each call site.
"""

from __future__ import annotations
import logging
import os
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Union
from app.llm.factory import get_provider, get_embedding_provider
from app.llm.config import config
from app.prompts.system import SYSTEM_PROMPT  # noqa: F401 -- re-exported as llm.SYSTEM_PROMPT
from app.core.circuit_breaker import CircuitBreaker as _CircuitBreaker

logger = logging.getLogger("diginyaya.llm")


def _llm_disabled() -> bool:
    """DIGINYAYA_USE_LLM=0 forces every provider call in this module to
    short-circuit without touching the network -- used by the eval harness
    (scripts/eval_cases.py) to guarantee a free, deterministic run instead
    of silently placing real billed Sarvam calls."""
    return os.environ.get("DIGINYAYA_USE_LLM", "1") == "0"


# See app.core.circuit_breaker.CircuitBreaker's docstring for the general
# rationale. This one guards generate()/generate_json()/generate_stream()
# below, which already catch every exception and degrade to None/empty
# (agents fall back to scripted behaviour) -- but each of those failing
# calls still pays the full provider timeout (config.timeout, tens of
# seconds) before giving up. A single dispute run makes several LLM calls
# across 5 agents; during a real Sarvam outage, this turns "one slow
# request" into "every agent in every concurrent case blocks for a timeout
# in turn."
_breaker = _CircuitBreaker(name="llm")


class _UsageTracker:
    """Process-wide accumulator for provider token usage.

    Populated from provider.last_usage() after each successful generate()/
    generate_json() call (see _record_usage() below). Only SarvamProvider
    currently reports real numbers (see app/llm/providers/sarvam.py); other
    providers' last_usage() returns None and is a no-op here. Used by
    scripts/measure_eval_cost.py to compare tokens-per-case against a
    committed baseline -- reset_totals() is called once per eval case so
    each case's usage can be read in isolation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    def record(self, usage: Optional[Dict[str, Any]]) -> None:
        if not usage:
            return
        with self._lock:
            self._totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self._totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            self._totals["total_tokens"] += int(usage.get("total_tokens") or 0)
            self._totals["calls"] += 1

    def totals(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._totals)

    def reset(self) -> None:
        with self._lock:
            self._totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


_usage = _UsageTracker()


def _record_usage() -> None:
    try:
        _usage.record(get_provider().last_usage())
    except Exception:
        pass  # usage tracking is a pure add-on -- never let it affect a real call's outcome


def get_usage_totals() -> Dict[str, int]:
    """Cumulative token usage recorded since the last reset_usage_totals()."""
    return _usage.totals()


def reset_usage_totals() -> None:
    """Zero the usage accumulator -- call before measuring a single unit of work (e.g. one eval case)."""
    _usage.reset()


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a text response using the active provider.

    Returns None (never raises) if the provider is unavailable, disabled
    via DIGINYAYA_USE_LLM=0, or the call fails -- callers (analysis.py,
    resolution.py) already treat a falsy result as "fall back to the
    scripted summary", the same contract generate_json() below documents.

    reasoning_effort defaults to None (disabled): Sarvam's reasoning model
    (sarvam-105b, the default for model=None) defaults to "medium" reasoning
    effort whenever this field is *omitted*, emitting a verbose
    reasoning_content trace before writing the final answer -- easily enough
    to consume the entire max_tokens budget on reasoning alone, leaving
    content: null. Passing None here is forwarded as an explicit
    reasoning_effort: null in the request body, which is what actually
    disables it (confirmed empirically: "low" is NOT reliably low for
    schema-constrained prompts -- it still starved a 600-token budget in
    testing, while explicit null produced correct output in ~50 tokens).
    Pass "low"/"medium"/"high" for call sites that genuinely want deeper
    reasoning at the cost of more tokens and latency.
    """
    if _llm_disabled() or not _breaker.allow():
        return None
    try:
        result = get_provider().generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
            reasoning_effort=reasoning_effort,
        )
        _breaker.record_success()
        _record_usage()
        return result
    except Exception:
        _breaker.record_failure()
        return None


def generate_json(
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
    Generate structured JSON using the active provider.

    Returns None (never raises) if the provider is unavailable or the
    model's output couldn't be parsed as JSON -- callers (mediation.py,
    resolution.py) already check `if data:` and fall back to scripted
    behaviour on None.

    See generate() above for why reasoning_effort defaults to None (disabled).
    """
    if _llm_disabled() or not _breaker.allow():
        return None
    try:
        result = get_provider().generate_json(
            prompt=prompt,
            system=system,
            schema=schema,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
            reasoning_effort=reasoning_effort,
        )
        _breaker.record_success()
        _record_usage()
        return result
    except Exception:
        _breaker.record_failure()
        return None


def generate_stream(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    Stream generated text.

    Never raises: if disabled, unavailable, or the provider fails before or
    during streaming, the generator simply yields nothing further so callers
    (graph.py's resolve_node) fall back via `acc or None`, same contract as
    generate() above.

    See generate() above for why reasoning_effort defaults to None (disabled).
    """
    if _llm_disabled() or not _breaker.allow():
        return
    try:
        yield from get_provider().generate_stream(
            prompt=prompt,
            system=system,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
            reasoning_effort=reasoning_effort,
        )
        _breaker.record_success()
    except Exception:
        _breaker.record_failure()
        return


def embed(text: Union[str, List[str]]) -> Optional[Union[List[float], List[List[float]]]]:
    """
    Generate embeddings.

    IMPORTANT: this is routed through get_embedding_provider(), NOT
    get_provider(). Embeddings always come from Ollama's nomic-embed-text
    locally, regardless of which provider (Sarvam/Ollama/Mock) is configured
    for chat -- Sarvam has no public embeddings endpoint, so routing this
    through the chat provider 404s whenever Sarvam is active.

    Returns None if no embedding-capable provider is reachable, so callers
    (rag/index.py) fall back to keyword search instead of crashing.

    Pass a list[str] to get back a list[list[float]] (one call per string,
    since providers only implement single-item embedding); pass a single
    str to get back a single list[float].
    """
    provider = get_embedding_provider()
    if provider is None:
        return None

    if isinstance(text, (list, tuple)):
        return [provider.embed(t) for t in text]
    return provider.embed(text)


_STATUS_CACHE_TTL = 20.0  # seconds
_status_cache: Optional[Dict[str, Any]] = None
_status_cache_at = 0.0
_status_cache_lock = threading.Lock()


def status() -> Dict[str, Any]:
    """
    Return provider diagnostics. Never raises: an explicitly-configured
    provider (e.g. DIGINYAYA_LLM_PROVIDER=sarvam) that can't even be
    constructed -- bad/missing key, unreachable host -- previously blew up
    LLMFactory.create() with an uncaught RuntimeError, which propagated all
    the way to a 500 on GET /api/ai-status. That's a read-only diagnostics
    endpoint; it should report "unavailable", not crash the request.

    Cached for _STATUS_CACHE_TTL seconds: the underlying provider call makes
    a real network round-trip to Sarvam (see SarvamProvider.is_available()),
    and the frontend calls this once on every page load -- with several
    concurrent users, that was a fresh live network call per page view for
    a value that's genuinely fine to be a few seconds stale.
    """
    global _status_cache, _status_cache_at
    now = time.monotonic()
    with _status_cache_lock:
        if _status_cache is not None and (now - _status_cache_at) < _STATUS_CACHE_TTL:
            return _status_cache

    try:
        result = get_provider().status()
    except Exception as exc:
        result = {
            "provider": config.provider,
            "available": False,
            "error": str(exc),
        }

    with _status_cache_lock:
        _status_cache = result
        _status_cache_at = now
    return result


def is_available() -> bool:
    """
    Check whether the active provider is available. Never raises, for the
    same reason as status() above.
    """
    if _llm_disabled():
        return False
    try:
        return get_provider().is_available()
    except Exception:
        return False


def provider_name() -> str:
    """
    Return the currently active provider.
    """
    return get_provider().__class__.__name__

def prewarm() -> bool:
    """
    Warm up the active provider.

    Called during application startup, on a background thread -- an
    unhandled exception here wouldn't crash the process (Python just logs
    the thread's traceback and moves on), but it silently defeats the point
    of prewarming and leaves no clear signal that provider construction
    itself failed. get_provider() is now inside the try for that reason.
    """

    try:
        provider = get_provider()
        return provider.is_available()
    except Exception:
        return False