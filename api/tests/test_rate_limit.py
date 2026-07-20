from collections import deque

from app.core import rate_limit


def test_sweep_evicts_idle_keys_but_keeps_active_ones(monkeypatch) -> None:
    rate_limit.reset_rate_limits()

    # Two idle windows seeded far in the past (fully expired), across two maps.
    rate_limit._tool_request_log[(1, "web_search")] = deque([100.0])
    rate_limit._request_log[999] = deque([100.0])

    # Jump past both the sliding window and the sweep interval so the periodic
    # sweep fires and the seeded windows are stale.
    future = 100.0 + rate_limit._SWEEP_INTERVAL_SECONDS + rate_limit.WINDOW_SECONDS + 1
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: future)

    # A still-active window (touched ~now) that must survive the sweep.
    rate_limit._tool_request_log[(3, "web_search")] = deque([future - 5])

    # Any rate-limit call triggers the sweep as a side effect.
    assert rate_limit.check_tool_rate_limit(2, "web_search", 10) is True

    assert (1, "web_search") not in rate_limit._tool_request_log
    assert 999 not in rate_limit._request_log
    # Both the active seeded key and the caller's own fresh key remain.
    assert (3, "web_search") in rate_limit._tool_request_log
    assert (2, "web_search") in rate_limit._tool_request_log


def test_sweep_is_throttled_within_the_interval(monkeypatch) -> None:
    rate_limit.reset_rate_limits()

    # First call establishes the last-sweep marker at t=1000.
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 1000.0)
    rate_limit.check_tool_rate_limit(2, "web_search", 10)

    # An idle key appears, and only a little time passes (< sweep interval).
    rate_limit._request_log[999] = deque([1000.0])
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 1000.0 + 10)
    rate_limit.check_tool_rate_limit(2, "web_search", 10)

    # Sweep hasn't run again yet, so the idle key is still present.
    assert 999 in rate_limit._request_log
