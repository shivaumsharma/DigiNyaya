"""Structured logging setup for the Language Gateway.

Every other ``app.language`` module (``cache``, ``detector``, ``translator``,
``gateway``) already logs through child loggers of ``"diginyaya.language"``
-- e.g. ``"diginyaya.language.cache"`` -- and already attaches structured
context via the standard ``extra={...}`` logging kwarg (event names,
language codes, request ids, cache hit/miss counts, and so on). This
module is the *sink* for all of that: it decides how those records
actually get formatted and where they go, so the other modules never need
to know or care -- they just log, as they already do.

Two output modes, controlled by ``config.enable_structured_logging``:

  - True (default): one JSON object per line, merging the standard fields
    (timestamp, level, logger, message) with whatever structured context
    was attached via ``extra=``. Safe for log aggregators (CloudWatch,
    ELK, Loki, etc.) to parse without a custom grok pattern.
  - False: a compact human-readable line for local development in a
    terminal, with the same ``extra=`` context appended as key=value
    pairs so nothing is silently lost when structured mode is off.

Settings are loaded from ``app.language.config``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.language.config import LanguageConfig, config as language_config

# The parent logger every app.language.* module logs into. Configuring
# *this* logger's handler/level/propagation is enough to control every
# child logger (cache, detector, translator, gateway) at once, since
# Python's logging module resolves effective handlers up the dotted name
# hierarchy -- individual modules never need to import this file.
LANGUAGE_LOGGER_NAME = "diginyaya.language"

# Attributes every logging.LogRecord carries by default. Anything else
# found on a record was attached via `extra={...}` by the caller and is
# treated as structured context worth surfacing in the output.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class StructuredFormatter(logging.Formatter):
    """Formats each record as a single-line JSON object.

    Merges the standard fields (timestamp, level, logger name, message)
    with any extra context attached via ``extra=``. Exceptions, if
    present, are included as a formatted traceback string under
    ``"exception"``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            payload[key] = self._json_safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Best-effort coercion so an odd `extra=` value (a Path, a
        dataclass, an object with no useful __str__) never crashes
        logging itself -- it just degrades to its string form.
        """
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)


class PlainFormatter(logging.Formatter):
    """Compact single-line formatter for local development.

    Still surfaces `extra=` context (as trailing key=value pairs) so
    nothing is lost when structured logging is turned off -- it's just
    less machine-parseable.
    """

    _base_fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self._base_fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }
        if not context:
            return base
        rendered = " ".join(f"{k}={v!r}" for k, v in sorted(context.items()))
        return f"{base}  ({rendered})"


_configured = False
_configure_lock = threading.Lock()


def configure_language_logging(
    settings: LanguageConfig | None = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Attach a handler + formatter + level to the ``"diginyaya.language"``
    logger tree, driven by ``config.enable_structured_logging`` /
    ``config.log_level``.

    Idempotent by default: safe to call from every module's import path
    without stacking duplicate handlers on repeated calls or repeated
    imports. Pass ``force=True`` to tear down and re-apply (e.g. tests
    that flip ``enable_structured_logging`` mid-run).

    Returns the configured parent logger.
    """
    global _configured
    settings = settings or language_config
    logger = logging.getLogger(LANGUAGE_LOGGER_NAME)

    with _configure_lock:
        if _configured and not force:
            return logger

        for handler in list(logger.handlers):
            logger.removeHandler(handler)

        handler = logging.StreamHandler()
        handler.setFormatter(
            StructuredFormatter()
            if settings.enable_structured_logging
            else PlainFormatter()
        )
        logger.addHandler(handler)
        logger.setLevel(settings.log_level)
        # This subsystem owns its own formatting. Leaving propagate=True
        # would also hand every record up to the root logger's handlers,
        # double-printing lines if the host app (e.g. uvicorn) configures
        # its own root handler -- so this subsystem's logs stay self
        # contained.
        logger.propagate = False

        _configured = True

    return logger


def get_logger(component: str) -> logging.Logger:
    """Return a child logger under ``"diginyaya.language.<component>"``,
    ensuring the parent has been configured first.

    Existing call sites that already do
    ``logging.getLogger("diginyaya.language.xxx")`` directly keep working
    unchanged -- configuring the parent logger affects the whole tree
    regardless of whether this function or ``configure_language_logging``
    ran before or after those child loggers were first created. This is
    just a convenience for new call sites that want the configuration
    guaranteed in one line.
    """
    configure_language_logging()
    return logging.getLogger(f"{LANGUAGE_LOGGER_NAME}.{component}")