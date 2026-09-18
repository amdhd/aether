"""Per-account backoff on failed logins.

The per-IP limiter bounds one caller. This bounds attempts against one account,
which rotating addresses would otherwise make unlimited — so these tests bypass
the IP limiter (resetting it between attempts) to exercise exactly the case it
cannot see.
"""

import fakeredis.aioredis
import pytest
from httpx import AsyncClient

from app.core import login_backoff, rate_limit
from app.core.login_backoff import (
    FAILURE_THRESHOLD,
    MAX_BACKOFF_SECONDS,
    MIN_BACKOFF_SECONDS,
    _backoff_seconds,
    _bucket,
    reset_login_backoff,
)

CREDENTIALS = {"email": "target@example.com", "name": "T", "password": "correct-horse-1"}


@pytest.fixture(autouse=True)
def _clean():
    reset_login_backoff()
    yield
    reset_login_backoff()
    rate_limit.set_redis_limiter_for_test(None)


async def _register(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/register", json=CREDENTIALS)
    assert resp.status_code == 201


async def _attempt(client: AsyncClient, email: str, password: str):
    # Reset the per-IP window each time: these tests stand in for an attacker
    # rotating addresses, which is precisely the case that limiter cannot catch.
    rate_limit.reset_rate_limits()
    return await client.post(
        "/api/v1/auth/login", data={"username": email, "password": password}
    )


def test_the_delay_grows_and_is_capped() -> None:
    assert _backoff_seconds(FAILURE_THRESHOLD) == MIN_BACKOFF_SECONDS
    assert _backoff_seconds(FAILURE_THRESHOLD + 1) == MIN_BACKOFF_SECONDS * 2
    assert _backoff_seconds(FAILURE_THRESHOLD + 2) == MIN_BACKOFF_SECONDS * 4
    # Capped, not unbounded: a locked-out real user waits rather than files a
    # ticket, because the address is attacker-choosable (see the module docs).
    assert _backoff_seconds(FAILURE_THRESHOLD + 50) == MAX_BACKOFF_SECONDS


def test_the_bucket_ignores_case_and_surrounding_space() -> None:
    """Otherwise varying the case of an address mints a fresh counter per
    attempt and the whole control is bypassed with a shift key."""
    assert _bucket("Target@Example.com") == _bucket("  target@example.com ")


def test_the_bucket_is_not_the_address_in_clear() -> None:
    """These keys would otherwise put a list of real user addresses into the
    Redis keyspace, visible in any dump or MONITOR session."""
    assert "target@example.com" not in _bucket("target@example.com")


async def test_repeated_failures_lock_the_account_out(client: AsyncClient) -> None:
    await _register(client)

    for _ in range(FAILURE_THRESHOLD - 1):
        assert (await _attempt(client, CREDENTIALS["email"], "wrong")).status_code == 401

    # The threshold'th failure is still a 401 — it is the attempt *after* the
    # delay starts that gets refused.
    assert (await _attempt(client, CREDENTIALS["email"], "wrong")).status_code == 401

    blocked = await _attempt(client, CREDENTIALS["email"], "wrong")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_a_locked_account_refuses_even_the_right_password(client: AsyncClient) -> None:
    """Otherwise the delay is free to skip: guess until locked, keep guessing."""
    await _register(client)
    for _ in range(FAILURE_THRESHOLD):
        await _attempt(client, CREDENTIALS["email"], "wrong")

    assert (await _attempt(client, CREDENTIALS["email"], CREDENTIALS["password"])).status_code == 429


async def test_an_unknown_address_locks_out_exactly_like_a_real_one(client: AsyncClient) -> None:
    """The enumeration property this control has to preserve.

    Login is timing-equalized so a missing account and a wrong password are
    indistinguishable. A lockout that applied only to real accounts would hand
    that straight back — "this address locks out, therefore it exists" is a far
    cleaner oracle than any timing difference.
    """
    await _register(client)

    for _ in range(FAILURE_THRESHOLD):
        assert (await _attempt(client, "nobody@example.com", "wrong")).status_code == 401

    unknown = await _attempt(client, "nobody@example.com", "wrong")

    reset_login_backoff()
    for _ in range(FAILURE_THRESHOLD):
        await _attempt(client, CREDENTIALS["email"], "wrong")
    known = await _attempt(client, CREDENTIALS["email"], "wrong")

    assert unknown.status_code == known.status_code == 429
    assert unknown.json() == known.json()


async def test_a_successful_login_clears_the_run(client: AsyncClient) -> None:
    """An attacker's failures must not keep delaying the person who owns the
    account once they prove they do."""
    await _register(client)

    for _ in range(FAILURE_THRESHOLD - 1):
        await _attempt(client, CREDENTIALS["email"], "wrong")

    assert (await _attempt(client, CREDENTIALS["email"], CREDENTIALS["password"])).status_code == 200

    # The counter is back to zero, so the next run gets the full allowance again
    # rather than tripping on its first failure.
    for _ in range(FAILURE_THRESHOLD):
        assert (await _attempt(client, CREDENTIALS["email"], "wrong")).status_code == 401


async def test_one_account_lockout_does_not_affect_another(client: AsyncClient) -> None:
    await _register(client)
    await client.post(
        "/api/v1/auth/register",
        json={"email": "bystander@example.com", "name": "B", "password": "correct-horse-2"},
    )

    for _ in range(FAILURE_THRESHOLD + 1):
        await _attempt(client, CREDENTIALS["email"], "wrong")

    ok = await _attempt(client, "bystander@example.com", "correct-horse-2")
    assert ok.status_code == 200


# --- backends ----------------------------------------------------------------


@pytest.fixture
def redis_backend():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    rate_limit.set_redis_limiter_for_test(rate_limit._RedisLimiter(client))
    yield client
    rate_limit.set_redis_limiter_for_test(None)


async def test_the_count_is_shared_through_redis(redis_backend) -> None:
    """Per-process counting is the fallback, not the design: a deployment with
    several tasks must not multiply the allowance by the task count."""
    for _ in range(FAILURE_THRESHOLD):
        await login_backoff.record_failure("shared@example.com")

    assert await login_backoff.seconds_until_retry("shared@example.com") > 0
    # Nothing local was consulted to reach that answer.
    reset_login_backoff()
    assert await login_backoff.seconds_until_retry("shared@example.com") > 0


async def test_a_redis_failure_degrades_to_local_counting() -> None:
    """Availability over strictness, matching the rate limiter: a Redis blip
    must not lock everybody out or wave everybody through."""

    class _DownClient:
        def pipeline(self, *args, **kwargs):
            raise ConnectionError("redis down")

        async def ttl(self, *args, **kwargs):
            raise ConnectionError("redis down")

    class _DownLimiter:
        _redis = _DownClient()

    rate_limit.set_redis_limiter_for_test(_DownLimiter())

    for _ in range(FAILURE_THRESHOLD):
        await login_backoff.record_failure("degraded@example.com")

    assert await login_backoff.seconds_until_retry("degraded@example.com") > 0
