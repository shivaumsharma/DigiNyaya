"""Local open-source LLM access (Ollama). Re-exports the client surface."""

from .client import (  # noqa: F401
    SYSTEM_PROMPT,
    embed,
    generate,
    generate_json,
    generate_stream,
    get_usage_totals,
    is_available,
    prewarm,
    reset_usage_totals,
    status,
)
