"""Minimal structured logging for the app.

Configures a single stdout handler on the `app` logger namespace (leaving
uvicorn's own loggers untouched) and hands out child loggers via `get_logger`.
Messages are written as greppable `key=value` fields rather than prose so they
can be parsed by log tooling without pulling in a JSON-logging dependency.
"""

import logging
import sys

_LOGGER_NAMESPACE = "app"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotently attach a stdout handler to the app logger namespace."""
    global _configured
    if _configured:
        return
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    # Don't double-emit through the root logger (e.g. uvicorn's config).
    logger.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the `app` namespace so it inherits the handler
    configured by `configure_logging`."""
    return logging.getLogger(name if name.startswith(_LOGGER_NAMESPACE) else f"{_LOGGER_NAMESPACE}.{name}")
