from collections import deque

import fakeredis.aioredis
import pytest

from app.core import rate_limit


@pytest.fixture(autouse=True)
def _in_memory_backend():
    # Default every test to the in-memory backend; Redis tests opt in explicitly.
    rate_limit.set_redis_limiter_for_test(None)
    rate_limit.reset_rate_limits()
    yield
    rate_limit.set_redis_limiter_for_test(None)
    rate_limit.reset_rate_limits()


# --- In-memory sweep (unchanged behaviour) ---------------------------------


async def test_sweep_evicts_idle_keys_but_keeps_active_ones(monkeypatch) -> None:
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
    assert await rate_limit.check_tool_rate_limit(2, "web_search", 10) is True

    assert (1, "web_search") not in rate_limit._tool_request_log
    assert 999 not in rate_limit._request_log
    assert (3, "web_search") in rate_limit._tool_request_log
    assert (2, "web_search") in rate_limit._tool_request_log


async def test_sweep_is_throttled_within_the_interval(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 1000.0)
    await rate_limit.check_tool_rate_limit(2, "web_search", 10)

    rate_limit._request_log[999] = deque([1000.0])
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 1000.0 + 10)
    await rate_limit.check_tool_rate_limit(2, "web_search", 10)

    # Sweep hasn't run again yet, so the idle key is still present.
    assert 999 in rate_limit._request_log


async def test_in_memory_denies_over_limit() -> None:
    for _ in range(3):
        assert await rate_limit.check_tool_rate_limit(7, "web_search", 3) is True
    assert await rate_limit.check_tool_rate_limit(7, "web_search", 3) is False


# --- Redis backend (via fakeredis) -----------------------------------------


@pytest.fixture
def redis_backend():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    rate_limit.set_redis_limiter_for_test(rate_limit._RedisLimiter(client))
    return client


async def test_redis_allows_up_to_limit_then_denies(redis_backend) -> None:
    limiter = rate_limit._get_redis_limiter()
    for _ in range(5):
        allowed, retry = await limiter.hit("rl:test", 5)
        assert allowed and retry == 0
    allowed, retry = await limiter.hit("rl:test", 5)
    assert not allowed
    assert retry >= 1  # told when to retry


async def test_redis_window_frees_up_after_entries_expire(redis_backend, monkeypatch) -> None:
    limiter = rate_limit._get_redis_limiter()
    base_ms = 1_000_000_000_000
    monkeypatch.setattr(rate_limit.time, "time", lambda: base_ms / 1000)
    assert (await limiter.hit("rl:test", 1))[0] is True
    assert (await limiter.hit("rl:test", 1))[0] is False  # window full

    # Advance past the window; the earlier entry ages out and a hit is allowed.
    monkeypatch.setattr(rate_limit.time, "time", lambda: (base_ms + 61_000) / 1000)
    assert (await limiter.hit("rl:test", 1))[0] is True


async def test_redis_shares_state_across_keys_for_same_user(redis_backend) -> None:
    # The public helper builds the same Redis key for a given (user, tool), so
    # two calls hit one shared window — the point of the Redis backend.
    assert await rate_limit.check_tool_rate_limit(42, "web_search", 1) is True
    assert await rate_limit.check_tool_rate_limit(42, "web_search", 1) is False


async def test_redis_failure_falls_back_to_in_memory() -> None:
    class _BoomLimiter:
        async def hit(self, key, limit):
            raise ConnectionError("redis down")

    rate_limit.set_redis_limiter_for_test(_BoomLimiter())
    # Despite Redis erroring, the call still enforces a limit (in-memory path).
    assert await rate_limit.check_tool_rate_limit(1, "web_search", 1) is True
    assert await rate_limit.check_tool_rate_limit(1, "web_search", 1) is False
