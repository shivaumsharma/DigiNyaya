"""Local open-source LLM access (Ollama). Re-exports the client surface."""

from .client import (  # noqa: F401
    SYSTEM_PROMPT,
    embed,
    generate,
    generate_json,
    generate_stream,
    is_available,
    prewarm,
    status,
)
