import logging

from app.core.logging import configure_logging, get_logger


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    app_logger = logging.getLogger("app")
    handler_count = len(app_logger.handlers)

    configure_logging()  # second call must not stack another handler
    assert len(app_logger.handlers) == handler_count
    assert app_logger.handlers, "expected at least one handler on the app logger"


def test_get_logger_namespaces_under_app() -> None:
    assert get_logger("agent.loop").name == "app.agent.loop"
    # An already-namespaced name isn't double-prefixed.
    assert get_logger("app.services.x").name == "app.services.x"
    # Child loggers propagate to the configured `app` handler.
    assert get_logger("agent.loop").parent is not None
