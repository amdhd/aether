import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import get_current_user
from app.core.config import settings
from app.models.user import User

WINDOW_SECONDS = 60.0

# Per-user sliding window of request timestamps. In-memory only: fine for a
# single API instance (see docker-compose.yml), would need a shared store
# (e.g. Redis) behind multiple instances.
_request_log: dict[int, deque[float]] = defaultdict(deque)

# Per-(user, tool) sliding window for agent tool calls that hit paid/external
# APIs (e.g. Tavily web_search), since a single chat turn can trigger several
# tool calls (see MAX_TOOL_ITERATIONS).
_tool_request_log: dict[tuple[int, str], deque[float]] = defaultdict(deque)

# Per-(client IP, endpoint) sliding window for unauthenticated auth endpoints
# (login/register), to slow down brute-force/enumeration attempts.
_auth_request_log: dict[tuple[str, str], deque[float]] = defaultdict(deque)

# These maps are keyed by user / (user, tool) / (client IP, endpoint) and each
# active window is trimmed on access — but a key whose owner goes idle keeps its
# now-empty deque forever, so without eviction the maps leak one entry per user
# and (for auth) per distinct client IP seen. Periodically sweep out keys whose
# window has fully expired to keep memory bounded on a long-lived process.
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


async def enforce_chat_rate_limit(current_user: User = Depends(get_current_user)) -> User:
    limit = settings.CHAT_RATE_LIMIT_PER_MINUTE
    now = time.monotonic()
    _sweep_expired(now)
    timestamps = _request_log[current_user.id]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= limit:
        retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0])))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please slow down and try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    timestamps.append(now)
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
        limit = settings.AUTH_RATE_LIMIT_PER_MINUTE
        client_ip = _client_ip(request)
        now = time.monotonic()
        _sweep_expired(now)
        timestamps = _auth_request_log[(client_ip, endpoint)]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= limit:
            retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)

    return _dependency


def check_tool_rate_limit(user_id: int, tool_name: str, limit: int) -> bool:
    """Sliding-window rate limit for agent tool calls. Returns True and
    records the call if the user is under `limit` calls to `tool_name` in
    the last minute, or False if they've hit the limit."""
    now = time.monotonic()
    _sweep_expired(now)
    timestamps = _tool_request_log[(user_id, tool_name)]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= limit:
        return False

    timestamps.append(now)
    return True
