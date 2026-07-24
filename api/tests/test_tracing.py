"""Tests for the tracing enable/disable gate.

Only the *disabled* path is exercised here: enabling tracing installs global
OpenTelemetry state and monkeypatches httpx/SQLAlchemy process-wide, which would
leak into the rest of the suite. The enabled path is smoke-tested manually (see
the module docstring in ``app/core/tracing.py``); what matters for the suite is
that the default path is a genuine no-op.
"""

import pytest

from app.core import tracing


def test_tracing_disabled_by_default() -> None:
    assert tracing.tracing_enabled() is False


def test_tracing_enabled_reads_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing.settings, "TRACING_ENABLED", True)
    assert tracing.tracing_enabled() is True


def test_configure_tracing_is_noop_when_disabled() -> None:
    # Passing objects that would explode if touched proves the disabled path
    # returns before instrumenting anything.
    sentinel = object()
    tracing.configure_tracing(sentinel, sentinel)  # type: ignore[arg-type]
    assert tracing._configured is False
