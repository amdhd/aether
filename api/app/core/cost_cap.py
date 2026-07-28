"""Per-user monthly spend cap for LLM usage.

The sliding-window rate limiter bounds *requests per minute*; it does nothing
about a user who stays politely under that limit and burns provider credit all
month. This closes that gap: before each chat turn, sum the user's token usage
for the current UTC calendar month, price it with the same configured rates the
cost metric uses, and refuse the turn once it crosses ``MONTHLY_COST_CAP_USD``.

The check is intentionally *pre-turn* and therefore approximate — the turn that
crosses the line is allowed to finish, so a user can end the month slightly over
the cap. Bounding a single turn matters less than bounding the month, and the
per-request message cap already limits how large one turn can get.
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.usage_log import UsageLog
from app.models.user import User

logger = get_logger(__name__)


def _month_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_to_date_cost_usd(db: AsyncSession, user_id: int, now: datetime | None = None) -> float:
    """Estimated USD spent by this user since the start of the current UTC month."""
    prompt_tokens, completion_tokens = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
            ).where(
                UsageLog.user_id == user_id,
                UsageLog.created_at >= _month_start(now),
            )
        )
    ).one()
    return metrics.estimate_cost_usd(prompt_tokens, completion_tokens)


async def enforce_monthly_cost_cap(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Reject the turn when the user is already over their monthly budget.

    A cap of 0 or less disables the check, so a self-hosted deployment paying its
    own provider bill can opt out.
    """
    cap = settings.MONTHLY_COST_CAP_USD
    if cap <= 0:
        return current_user

    spent = await month_to_date_cost_usd(db, current_user.id)
    if spent >= cap:
        logger.warning(
            "cost_cap.exceeded user_id=%s spent_usd=%s cap_usd=%s",
            current_user.id,
            spent,
            cap,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "You've reached your monthly usage limit for AI chat. "
                "It resets at the start of next month."
            ),
        )
    return current_user
