"""Progressive per-account backoff on failed logins.

``app.core.rate_limit`` meters auth endpoints per IP. That bounds one caller; it
does nothing about a chosen *account*. An attacker with rotating addresses — a
botnet, a proxy pool, a phone on mobile data — stays under 10 requests a minute
from every address while making unlimited attempts against one email. Bcrypt
makes each guess expensive, which is real mitigation and is why this is a second
layer rather than the only one, but nothing counted failures per account at all.

This does. Two properties are worth stating because they are what make it safe:

**It keys on the submitted address whether or not an account exists.** Login is
carefully timing-equalized so a missing account and a wrong password are
indistinguishable (``core.security.fake_verify_password``), and a lockout that
applied only to real accounts would hand that back — "this address locks out,
therefore it exists" is a cleaner oracle than any timing difference. Counting
every address means the response is identical either way; what an attacker
learns is that *they* have been failing, which they already knew.

**It expires on its own.** Keying on an attacker-chosen address is what closes
the oracle, and the price is that anyone can deliberately lock an account they
know the address of. That makes an admin-unlock lockout the wrong shape: it
converts a nuisance into a support ticket. The delay is capped, self-healing, and
sits behind the per-IP limiter that still makes the attempt costly, so the worst
case is a bounded wait rather than a dead account.

Backends mirror the rate limiter: Redis when ``REDIS_URL`` is set so the count
holds across tasks, per-process otherwise, and a Redis failure degrades to the
local counter rather than failing the login outright.
"""

import hashlib
import math
import time

from app.core.logging import get_logger
from app.core.rate_limit import get_redis_client

logger = get_logger(__name__)

# Free attempts before any delay. High enough that a person who mistypes a
# password twice and then goes to find it in their manager is never delayed.
FAILURE_THRESHOLD = 5

# How long a run of failures is remembered. Counting forever would mean five
# typos spread over a year eventually locking someone out.
FAILURE_TTL_SECONDS = 3600

# The delay doubles per failure past the threshold, from one minute to fifteen.
# Fifteen minutes turns an unlimited online attack into roughly 100 guesses a
# day, which is no longer worth running, while staying short enough that a
# locked-out real user waits rather than files a ticket.
MIN_BACKOFF_SECONDS = 60
MAX_BACKOFF_SECONDS = 900

_failures: dict[str, tuple[int, float]] = {}
_blocked_until: dict[str, float] = {}


def reset_login_backoff() -> None:
    _failures.clear()
    _blocked_until.clear()


def _bucket(email: str) -> str:
    """Hash the normalised address.

    Normalised because otherwise varying the case of an address would mint a
    fresh counter per attempt and defeat the whole thing. Hashed because these
    keys otherwise put a list of real user addresses into the Redis keyspace,
    where they show up in any dump, MONITOR session or slow-log line.
    """
    return hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:32]


def _fail_key(bucket: str) -> str:
    return f"login:fail:{bucket}"


def _block_key(bucket: str) -> str:
    return f"login:block:{bucket}"


def _backoff_seconds(count: int) -> int:
    over = count - FAILURE_THRESHOLD
    return min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * (2**over))


async def seconds_until_retry(email: str) -> int:
    """How long this address must wait, or 0 if it may attempt now."""
    bucket = _bucket(email)

    redis = get_redis_client()
    if redis is not None:
        try:
            ttl = await redis.ttl(_block_key(bucket))
            return ttl if ttl and ttl > 0 else 0
        except Exception as exc:
            logger.warning("login_backoff.redis_failed error=%r; using in-memory state", exc)

    until = _blocked_until.get(bucket)
    if until is None:
        return 0
    remaining = until - time.time()
    if remaining <= 0:
        _blocked_until.pop(bucket, None)
        return 0
    return max(1, math.ceil(remaining))


async def record_failure(email: str) -> None:
    """Count a failed attempt and, past the threshold, start the next delay."""
    bucket = _bucket(email)

    redis = get_redis_client()
    if redis is not None:
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(_fail_key(bucket))
                pipe.expire(_fail_key(bucket), FAILURE_TTL_SECONDS)
                count, _ = await pipe.execute()
            if count >= FAILURE_THRESHOLD:
                await redis.set(_block_key(bucket), "1", ex=_backoff_seconds(count))
                logger.warning(
                    "login_backoff.locked failures=%s backoff_s=%s", count, _backoff_seconds(count)
                )
            return
        except Exception as exc:
            logger.warning("login_backoff.redis_failed error=%r; using in-memory state", exc)

    now = time.time()
    count, last_seen = _failures.get(bucket, (0, 0.0))
    if now - last_seen > FAILURE_TTL_SECONDS:
        count = 0
    count += 1
    _failures[bucket] = (count, now)
    if count >= FAILURE_THRESHOLD:
        _blocked_until[bucket] = now + _backoff_seconds(count)
        logger.warning(
            "login_backoff.locked failures=%s backoff_s=%s", count, _backoff_seconds(count)
        )


async def clear(email: str) -> None:
    """Forget this address's failures. Called on a successful login, so an
    attacker's failed run never delays the person who actually owns it."""
    bucket = _bucket(email)

    redis = get_redis_client()
    if redis is not None:
        try:
            await redis.delete(_fail_key(bucket), _block_key(bucket))
            return
        except Exception as exc:
            logger.warning("login_backoff.redis_failed error=%r; using in-memory state", exc)

    _failures.pop(bucket, None)
    _blocked_until.pop(bucket, None)
