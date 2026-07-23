"""Sliding-window rate limiting with a pluggable store.

Two backends behind one API:

* **In-memory** (default) — per-process deques of request timestamps. Correct for
  a single API instance (local dev, tests, the docker-compose stack).
* **Redis** — used when ``REDIS_URL`` is set. Enforces each window *globally* via
  a sorted set per key, so the limit holds across every API task. This is what
  makes the limits real once the service scales past one instance (the HA shape
  runs multiple Fargate tasks behind the ALB).

The Redis backend degrades safely: if Redis is unreachable, a limiter call logs a
warning and falls back to the in-memory window for that call, so a Redis blip
throttles per-instance rather than 500-ing every request.
"""

import math
import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

WINDOW_SECONDS = 60.0

# --- In-memory backend ------------------------------------------------------

# Per-user sliding window of request timestamps (chat endpoint).
_request_log: dict[int, deque[float]] = defaultdict(deque)
# Per-(user, tool) window for agent tool calls hitting paid/external APIs.
_tool_request_log: dict[tuple[int, str], deque[float]] = defaultdict(deque)
# Per-(client IP, endpoint) window for unauthenticated auth endpoints.
_auth_request_log: dict[tuple[str, str], deque[float]] = defaultdict(deque)

# Each active window is trimmed on access, but a key whose owner goes idle keeps
# its now-empty deque forever. Periodically sweep fully-expired keys so the maps
# stay bounded on a long-lived process.
_SWEEP_INTERVAL_SECONDS = 300.0
_last_sweep = 0.0


def reset_rate_limits() -> None:
    global _last_sweep
    _request_log.clear()
    _tool_request_log.clear()
    _auth_request_log.clear()
    _last_sweep = 0.0


def _sweep_expired(now: float) -> None:
    """Drop keys whose sliding window is fully expired (or already empty), so
    idle users/IPs don't accumulate. Runs at most once per _SWEEP_INTERVAL."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    for log in (_request_log, _tool_request_log, _auth_request_log):
        stale = [key for key, ts in log.items() if not ts or now - ts[-1] >= WINDOW_SECONDS]
        for key in stale:
            del log[key]


def _inmemory_hit(log: dict, key, limit: int) -> tuple[bool, int]:
    """Record a hit in an in-memory window. Returns (allowed, retry_after_secs).

    Allows exactly ``limit`` requests per WINDOW_SECONDS; the next is denied with
    the seconds until the oldest recorded hit falls out of the window.
    """
    now = time.monotonic()
    _sweep_expired(now)
    timestamps = log[key]
    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()
    if len(timestamps) >= limit:
        retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0])))
        return False, retry_after
    timestamps.append(now)
    return True, 0


# --- Redis backend ----------------------------------------------------------


class _RedisLimiter:
    """Global sliding window backed by one Redis sorted set per key.

    Members are unique per request, scored by wall-clock ms. Each hit, in a
    single transaction: drop entries older than the window, add this request,
    count the window, and refresh the key's TTL. If the count exceeds the limit
    the just-added member is rolled back so it doesn't count against later
    requests, and the caller is told when the oldest entry expires.
    """

    def __init__(self, client) -> None:
        self._redis = client

    @classmethod
    def from_url(cls, url: str) -> "_RedisLimiter":
        import redis.asyncio as aioredis

        return cls(aioredis.from_url(url, decode_responses=True))

    async def hit(self, key: str, limit: int) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000)
        window_ms = int(WINDOW_SECONDS * 1000)
        member = f"{now_ms}-{uuid.uuid4().hex}"

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now_ms - window_ms)
            pipe.zadd(key, {member: now_ms})
            pipe.zcard(key)
            pipe.pexpire(key, window_ms)
            _, _, count, _ = await pipe.execute()

        if count <= limit:
            return True, 0

        # Over the limit: undo our own hit (so it doesn't penalise the next
        # caller) and report when the oldest in-window entry ages out.
        await self._redis.zrem(key, member)
        oldest = await self._redis.zrange(key, 0, 0, withscores=True)
        if oldest:
            retry_after = max(1, math.ceil((int(oldest[0][1]) + window_ms - now_ms) / 1000))
        else:
            retry_after = 1
        return False, retry_after


_redis_limiter: _RedisLimiter | None = None
_redis_limiter_ready = False


def _get_redis_limiter() -> _RedisLimiter | None:
    """Lazily build the Redis limiter from REDIS_URL, once. None = in-memory."""
    global _redis_limiter, _redis_limiter_ready
    if not _redis_limiter_ready:
        _redis_limiter_ready = True
        if settings.REDIS_URL:
            _redis_limiter = _RedisLimiter.from_url(settings.REDIS_URL)
    return _redis_limiter


def set_redis_limiter_for_test(limiter: _RedisLimiter | None) -> None:
    """Test hook: inject (or clear) the Redis limiter without a real REDIS_URL."""
    global _redis_limiter, _redis_limiter_ready
    _redis_limiter = limiter
    _redis_limiter_ready = True


async def _hit(inmemory_log: dict, inmemory_key, redis_key: str, limit: int) -> tuple[bool, int]:
    """Record a hit against the active backend, falling back to in-memory if a
    configured Redis is unreachable."""
    limiter = _get_redis_limiter()
    if limiter is not None:
        try:
            return await limiter.hit(redis_key, limit)
        except Exception as exc:
            logger.warning("rate_limit.redis_failed error=%r; using in-memory fallback", exc)
    return _inmemory_hit(inmemory_log, inmemory_key, limit)


# --- Public API -------------------------------------------------------------


async def enforce_chat_rate_limit(current_user: User = Depends(get_current_user)) -> User:
    allowed, retry_after = await _hit(
        _request_log, current_user.id, f"rl:chat:{current_user.id}", settings.CHAT_RATE_LIMIT_PER_MINUTE
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please slow down and try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )
    return current_user


def _client_ip(request: Request) -> str:
    """Best-effort originating client IP. Behind a trusted proxy the socket peer
    is the proxy, so the real client is the first entry of X-Forwarded-For.
    Only honored when TRUST_PROXY_HEADERS is set, since the header is otherwise
    attacker-controlled and would let anyone forge a fresh IP per request."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_auth_rate_limit(endpoint: str):
    """Returns a dependency that rate-limits an unauthenticated auth endpoint
    (login/register) per client IP, to slow down brute-force/enumeration."""

    async def _dependency(request: Request) -> None:
        client_ip = _client_ip(request)
        allowed, retry_after = await _hit(
            _auth_request_log,
            (client_ip, endpoint),
            f"rl:auth:{client_ip}:{endpoint}",
            settings.AUTH_RATE_LIMIT_PER_MINUTE,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency


async def check_tool_rate_limit(user_id: int, tool_name: str, limit: int) -> bool:
    """Sliding-window rate limit for agent tool calls. Returns True and records
    the call if the user is under `limit` calls to `tool_name` in the last
    minute, else False."""
    allowed, _ = await _hit(
        _tool_request_log, (user_id, tool_name), f"rl:tool:{user_id}:{tool_name}", limit
    )
    return allowed
