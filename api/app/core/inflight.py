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


async def acquire_turn_slot(user_id: int) -> bool:
    """Claim a slot for one chat turn. False means the user is already at the limit."""
    limit = settings.CHAT_MAX_CONCURRENT_TURNS
    if limit <= 0:
        return True

    redis = get_redis_client()
    if redis is not None:
        try:
            count = await redis.incr(_key(user_id))
            # Refresh on every claim: the window that matters is "time since the
            # last turn started", not time since the first.
            await redis.expire(_key(user_id), SLOT_TTL_SECONDS)
            if count > limit:
                await redis.decr(_key(user_id))
                return False
            return True
        except Exception as exc:
            # Same posture as the rate limiter: a Redis blip degrades to a
            # per-instance ceiling rather than failing the request outright.
            logger.warning("inflight.redis_failed error=%r; using in-memory fallback", exc)

    if _inflight[user_id] >= limit:
        return False
    _inflight[user_id] += 1
    return True


async def release_turn_slot(user_id: int) -> None:
    """Give the slot back. Safe to call even if the claim went to the other backend."""
    if settings.CHAT_MAX_CONCURRENT_TURNS <= 0:
        return

    redis = get_redis_client()
    if redis is not None:
        try:
            if await redis.decr(_key(user_id)) < 0:
                # Never let a stray release push the counter negative, or the
                # next TTL window would hand out extra slots.
                await redis.set(_key(user_id), 0, ex=SLOT_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("inflight.redis_failed error=%r; releasing in-memory slot", exc)

    if _inflight[user_id] > 0:
        _inflight[user_id] -= 1
    if _inflight[user_id] == 0:
        _inflight.pop(user_id, None)
