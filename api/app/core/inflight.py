"""Per-user ceiling on concurrently running chat turns.

This exists to make the monthly cost cap hold up. That check reads a user's
month-to-date spend and allows the turn, but a turn's tokens are only written to
``UsageLog`` once it *finishes* — so N requests fired at the same moment all read
the same total, all pass, and all run. Opening tabs was enough to overshoot the
cap by a multiple.

Bounding how many turns a user can have in flight bounds that overshoot to
``CHAT_MAX_CONCURRENT_TURNS`` turns, which is the same order as the pre-turn
tolerance the cap already documents.

Backends mirror ``app.core.rate_limit``: Redis when ``REDIS_URL`` is set, so the
ceiling holds across every API task, and a per-process counter otherwise. Slots
are released in the streaming generator's ``finally``; the Redis key also carries
a TTL so a process killed mid-turn can't strand a slot forever.
"""

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import get_redis_client

logger = get_logger(__name__)

# Longer than any plausible turn (the agent loop is bounded by MAX_TOOL_ITERATIONS
# and the provider's own timeouts), so this only ever reclaims leaked slots.
SLOT_TTL_SECONDS = 600

_inflight: dict[int, int] = defaultdict(int)


def reset_inflight() -> None:
    _inflight.clear()


def _key(user_id: int) -> str:
    return f"inflight:chat:{user_id}"


class SlotBackend(Enum):
    """Which counter granted a slot, and therefore which one must take it back."""

    REDIS = "redis"
    MEMORY = "memory"
    # The ceiling is switched off; nothing was counted and nothing is owed back.
    DISABLED = "disabled"


@dataclass(frozen=True)
class TurnSlot:
    """A claimed turn slot. Carried from the route to the streaming generator so
    the release can find the counter the claim actually came from."""

    user_id: int
    backend: SlotBackend




async def acquire_turn_slot(user_id: int) -> TurnSlot | None:
    """Claim a slot for one chat turn. None means the user is already at the limit.

    The handle records *which* counter granted the slot, because that is the one
    that has to take it back. Returning a bare bool was the bug: the claim falls
    back to the per-process counter when Redis is unreachable, while the release
    tried Redis first and stopped there if it worked. Redis down at claim and up
    at release therefore incremented the local counter and decremented the remote
    one, leaking a slot every time — and after CHAT_MAX_CONCURRENT_TURNS such
    blips the user could not chat again until the task restarted.
    """
    limit = settings.CHAT_MAX_CONCURRENT_TURNS
    if limit <= 0:
        return TurnSlot(user_id, SlotBackend.DISABLED)

    redis = get_redis_client()
    if redis is not None:
        try:
            # INCR and EXPIRE in one transaction, the same shape
            # _RedisLimiter.hit uses. As two round trips, a process dying
            # between them left a key with no TTL — and that key strands the
            # slot until the same user happens to claim again, which is
            # precisely when they are already locked out.
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(_key(user_id))
                # Refreshed on every claim: the window that matters is "time
                # since the last turn started", not since the first.
                pipe.expire(_key(user_id), SLOT_TTL_SECONDS)
                count, _ = await pipe.execute()

            if count <= limit:
                return TurnSlot(user_id, SlotBackend.REDIS)

            # Over the limit: give back the increment so it doesn't count
            # against the next caller. Outside the transaction, like hit()'s
            # own rollback — a concurrent claim in that gap sees an inflated
            # count and gets a 429 it can retry, which is a false negative on
            # the safe side, not a leaked slot.
            await redis.decr(_key(user_id))
            return None
        except Exception as exc:
            # Same posture as the rate limiter: a Redis blip degrades to a
            # per-instance ceiling rather than failing the request outright.
            logger.warning("inflight.redis_failed error=%r; using in-memory fallback", exc)

    if _inflight[user_id] >= limit:
        return None
    _inflight[user_id] += 1
    return TurnSlot(user_id, SlotBackend.MEMORY)


async def release_turn_slot(slot: TurnSlot | None) -> None:
    """Give a slot back to the counter that granted it.

    None is accepted so the caller can release unconditionally without first
    working out whether it ever held one.
    """
    if slot is None or slot.backend is SlotBackend.DISABLED:
        return

    if slot.backend is SlotBackend.REDIS:
        await _release_redis(slot.user_id)
        return

    if _inflight[slot.user_id] > 0:
        _inflight[slot.user_id] -= 1
    if _inflight[slot.user_id] == 0:
        _inflight.pop(slot.user_id, None)


async def _release_redis(user_id: int) -> None:
    redis = get_redis_client()
    if redis is not None:
        try:
            if await redis.decr(_key(user_id)) < 0:
                # Never let a stray release push the counter negative, or the
                # next TTL window would hand out extra slots.
                await redis.set(_key(user_id), 0, ex=SLOT_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("inflight.redis_failed error=%r; slot will expire with its TTL", exc)

    # Redis granted this slot and Redis cannot take it back, so the key's TTL is
    # the backstop. Decrementing the in-memory counter instead would free a slot
    # it never granted — which is the same class of mistake this handle exists to
    # prevent, just pointing the other way.
