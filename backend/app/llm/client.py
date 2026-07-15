"""
Public LLM client interface.

This module exposes a stable API for the rest of DigiNyaya.

Agents should ONLY import from this module and never interact
with providers directly.

Changing providers (Ollama, Sarvam, OpenAI, etc.) should never
require changes outside the llm package.

Call convention (matches every call site in app/agents and app/rag):

    llm.generate(prompt, system=llm.SYSTEM_PROMPT, max_tokens=160)
    llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=220)
    llm.generate_stream(prompt, system=llm.SYSTEM_PROMPT, max_tokens=260)
    llm.embed(["doc one", "doc two", ...])   # -> list[list[float]]
    llm.embed("single string")               # -> list[float]

`prompt` is always positional; `system` and everything else is keyword-only.
This used to not match what the agents called with (they passed system as a
second positional arg, plus a max_tokens kwarg the old signature didn't
accept at all) -- that mismatch is what crashed every LLM-touching agent
call with a TypeError. Fixed here + at each call site.
"""

from __future__ import annotations
import os
from typing import Any, Dict, Generator, List, Optional, Union
from app.llm.factory import get_provider, get_embedding_provider
from app.llm.config import config
from app.prompts.system import SYSTEM_PROMPT


def _llm_disabled() -> bool:
    """DIGINYAYA_USE_LLM=0 forces every provider call in this module to
    short-circuit without touching the network -- used by the eval harness
    (scripts/eval_cases.py) to guarantee a free, deterministic run instead
    of silently placing real billed Sarvam calls."""
    return os.environ.get("DIGINYAYA_USE_LLM", "1") == "0"


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """
    Generate a text response using the active provider.

    Returns None (never raises) if the provider is unavailable, disabled
    via DIGINYAYA_USE_LLM=0, or the call fails -- callers (analysis.py,
    resolution.py) already treat a falsy result as "fall back to the
    scripted summary", the same contract generate_json() below documents.
    """
    if _llm_disabled():
        return None
    try:
        return get_provider().generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
        )
    except Exception:
        return None


def generate_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
    temperature: float = 0.0,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate structured JSON using the active provider.

    Returns None (never raises) if the provider is unavailable or the
    model's output couldn't be parsed as JSON -- callers (mediation.py,
    resolution.py) already check `if data:` and fall back to scripted
    behaviour on None.
    """
    if _llm_disabled():
        return None
    try:
        return get_provider().generate_json(
            prompt=prompt,
            system=system,
            schema=schema,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
        )
    except Exception:
        return None


def generate_stream(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Generator[str, None, None]:
    """
    Stream generated text.

    Never raises: if disabled, unavailable, or the provider fails before or
    during streaming, the generator simply yields nothing further so callers
    (graph.py's resolve_node) fall back via `acc or None`, same contract as
    generate() above.
    """
    if _llm_disabled():
        return
    try:
        yield from get_provider().generate_stream(
            prompt=prompt,
            system=system,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens or config.max_tokens,
        )
    except Exception:
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


def status() -> Dict[str, Any]:
    """
    Return provider diagnostics. Never raises: an explicitly-configured
    provider (e.g. DIGINYAYA_LLM_PROVIDER=sarvam) that can't even be
    constructed -- bad/missing key, unreachable host -- previously blew up
    LLMFactory.create() with an uncaught RuntimeError, which propagated all
    the way to a 500 on GET /api/ai-status. That's a read-only diagnostics
    endpoint; it should report "unavailable", not crash the request.
    """
    try:
        return get_provider().status()
    except Exception as exc:
        return {
            "provider": config.provider,
            "available": False,
            "error": str(exc),
        }


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