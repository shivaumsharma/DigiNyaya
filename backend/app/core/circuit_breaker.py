"""Generic process-wide circuit breaker for calls to an external provider.

Extracted from app.llm.client's original private _CircuitBreaker (which now
imports this) so the same fail-fast behaviour can protect every direct
Sarvam call in the codebase, not just chat/JSON generation -- Document AI
OCR, Speech-to-Text, translation, and language detection each hit a
different Sarvam endpoint with independently-failing uptime, and none of
them had this protection: a sustained outage on any one of them meant every
caller paid the full timeout (or, for the translator/detector, a full
retry-with-backoff loop) before degrading, every single time.

Each protected call site should own its own CircuitBreaker instance --
sharing one breaker across unrelated endpoints (e.g. Document AI and
Speech-to-Text) would trip translation off because OCR is down, which is
not the correct failure correlation.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("diginyaya.circuit_breaker")


class CircuitBreaker:
    """After ``failure_threshold`` consecutive failures, calls fail fast
    (``allow()`` returns False) for ``cooldown_seconds`` instead of paying
    another timeout. One trial call after cooldown decides whether to close
    the breaker again (success) or re-open it for another cooldown window.
    """

    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 30.0

    def __init__(
        self,
        *,
        name: str = "circuit_breaker",
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        with self._lock:
            return time.monotonic() >= self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold and self._open_until < time.monotonic():
                self._open_until = time.monotonic() + self.cooldown_seconds
                logger.warning(
                    "circuit breaker opened",
                    extra={
                        "event": "circuit_breaker_open",
                        "breaker": self.name,
                        "consecutive_failures": self._consecutive_failures,
                        "cooldown_seconds": self.cooldown_seconds,
                    },
                )
