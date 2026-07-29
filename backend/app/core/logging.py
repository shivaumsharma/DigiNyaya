"""Structured (JSON) logging for the app at large.

app.language already has its own StructuredFormatter (app/language/logging.py),
deliberately self-contained -- see that module's docstring -- so it isn't
reused here; this is the equivalent sink for everything else: the
orchestrator's agent decisions/confidence scores and jobs.py's case status
transitions, both otherwise invisible to any log aggregator.

One JSON object per line, merging the standard fields (timestamp, level,
logger, message) with whatever structured context a call site attaches via
the standard `extra={...}` logging kwarg -- e.g.
``logger.info("agent done", extra={"event": "agent_done", "case_id": ...,
"agent": "analysis", "confidence": 0.82})``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

APP_LOGGER_NAME = "diginyaya"

_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False
_configure_lock = threading.Lock()


def configure_app_logging(*, force: bool = False) -> logging.Logger:
    """Attach a JSON-formatting handler to the "diginyaya" logger tree.

    Every module that does ``logging.getLogger("diginyaya.<x>")`` (jobs,
    the orchestrator, etc.) is a child of this logger and picks up its
    formatting automatically, the same pattern app.language uses for its
    own subtree. Idempotent by default so re-importing never stacks
    duplicate handlers; pass force=True to reconfigure (tests).
    """
    global _configured
    logger = logging.getLogger(APP_LOGGER_NAME)

    with _configure_lock:
        if _configured and not force:
            return logger

        for handler in list(logger.handlers):
            logger.removeHandler(handler)

        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.getenv("DIGINYAYA_LOG_LEVEL", "INFO").upper())
        # app.language configures its own subtree independently (see that
        # module) -- propagate=False here just stops *this* logger's
        # records from also reaching the root logger's handlers (e.g.
        # uvicorn's), which would otherwise double-print every line.
        logger.propagate = False

        _configured = True

    return logger
