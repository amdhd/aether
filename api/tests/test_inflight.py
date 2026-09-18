"""The per-user ceiling that makes the monthly cost cap enforceable.

Without it, simultaneous turns all read the same month-to-date spend — which is
only written once a turn *ends* — so they all pass the cap together.
"""

import fakeredis.aioredis
import pytest

from app.core import rate_limit
from app.core.config import settings
from app.core.inflight import (
    SlotBackend,
    acquire_turn_slot,
    release_turn_slot,
    reset_inflight,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean_slots():
    reset_inflight()
    yield
    reset_inflight()


async def test_allows_up_to_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 2)

    assert await acquire_turn_slot(1) is not None
    assert await acquire_turn_slot(1) is not None


async def test_rejects_the_turn_past_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 2)

    await acquire_turn_slot(1)
    await acquire_turn_slot(1)
    # This is the request that would otherwise race the cost cap.
    assert await acquire_turn_slot(1) is None


async def test_releasing_frees_the_slot_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    slot = await acquire_turn_slot(1)
    assert slot is not None
    assert await acquire_turn_slot(1) is None

    await release_turn_slot(slot)
    assert await acquire_turn_slot(1) is not None


async def test_limit_is_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    assert await acquire_turn_slot(1) is not None
    # One busy user must not block everybody else.
    assert await acquire_turn_slot(2) is not None


async def test_extra_releases_cannot_bank_spare_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    slot = await acquire_turn_slot(1)
    await release_turn_slot(slot)
    # The same handle released twice — a retry, a double finally — must not
    # bank a spare.
    await release_turn_slot(slot)

    assert await acquire_turn_slot(1) is not None
    assert await acquire_turn_slot(1) is None


async def test_zero_disables_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 0)

    for _ in range(5):
        assert await acquire_turn_slot(1) is not None


# --- Redis-backed slots, and the flap between the two backends ----------------


@pytest.fixture
def redis_backend():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    rate_limit.set_redis_limiter_for_test(rate_limit._RedisLimiter(client))
    yield client
    rate_limit.set_redis_limiter_for_test(None)


async def test_redis_grants_and_releases_a_slot(redis_backend, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    slot = await acquire_turn_slot(1)
    assert slot is not None and slot.backend is SlotBackend.REDIS
    assert await acquire_turn_slot(1) is None

    await release_turn_slot(slot)
    assert await acquire_turn_slot(1) is not None


async def test_the_claim_sets_a_ttl_in_the_same_round_trip(redis_backend, monkeypatch) -> None:
    """INCR then EXPIRE as two commands leaves a key with no TTL if the process
    dies between them, and that key strands the slot until someone claims again."""
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 2)

    await acquire_turn_slot(7)
    assert await redis_backend.ttl("inflight:chat:7") > 0


class _DownClient:
    """A Redis that is unreachable, so the claim falls through to the local counter."""

    def pipeline(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def decr(self, *args, **kwargs):
        raise ConnectionError("redis down")


class _DownLimiter:
    _redis = _DownClient()


async def test_a_slot_granted_in_memory_is_released_in_memory(monkeypatch) -> None:
    """The leak this handle exists to prevent: Redis down when the turn starts
    and back up when it ends. The claim fell through to the local counter; the
    release used to try Redis first, succeed, and never touch the local one — so
    the slot leaked, and CHAT_MAX_CONCURRENT_TURNS such blips locked the user out
    of chat until the task restarted."""
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    rate_limit.set_redis_limiter_for_test(_DownLimiter())
    slot = await acquire_turn_slot(1)
    assert slot is not None and slot.backend is SlotBackend.MEMORY

    # Redis is healthy again by the time the turn ends.
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    rate_limit.set_redis_limiter_for_test(rate_limit._RedisLimiter(client))
    await release_turn_slot(slot)

    # The release did not go to Redis, which never granted anything — and would
    # have been driven to -1 by a decrement it did not owe.
    assert await client.get("inflight:chat:1") is None

    # With Redis unreachable again, the claim goes back to the local counter —
    # the only backend that can show whether the release actually landed. A
    # healthy Redis here would grant the slot either way and prove nothing.
    rate_limit.set_redis_limiter_for_test(_DownLimiter())
    assert await acquire_turn_slot(1) is not None


async def test_a_redis_slot_is_not_released_against_the_local_counter(monkeypatch) -> None:
    """The same mistake pointing the other way: Redis granted it, Redis is down
    at release. The key's TTL is the backstop — freeing a local slot instead
    would hand out one the local counter never granted."""
    monkeypatch.setattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 1)

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    rate_limit.set_redis_limiter_for_test(rate_limit._RedisLimiter(client))
    try:
        slot = await acquire_turn_slot(1)
        assert slot is not None and slot.backend is SlotBackend.REDIS
    finally:
        rate_limit.set_redis_limiter_for_test(_DownLimiter())

    await release_turn_slot(slot)

    # The local counter was never credited a slot it did not grant, so a fresh
    # claim on the in-memory backend starts from zero rather than from -1.
    assert await acquire_turn_slot(1) is not None
    assert await acquire_turn_slot(1) is None
