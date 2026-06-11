import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status

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


def reset_rate_limits() -> None:
    _request_log.clear()
    _tool_request_log.clear()


async def enforce_chat_rate_limit(current_user: User = Depends(get_current_user)) -> User:
    limit = settings.CHAT_RATE_LIMIT_PER_MINUTE
    now = time.monotonic()
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


def check_tool_rate_limit(user_id: int, tool_name: str, limit: int) -> bool:
    """Sliding-window rate limit for agent tool calls. Returns True and
    records the call if the user is under `limit` calls to `tool_name` in
    the last minute, or False if they've hit the limit."""
    now = time.monotonic()
    timestamps = _tool_request_log[(user_id, tool_name)]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= limit:
        return False

    timestamps.append(now)
    return True
