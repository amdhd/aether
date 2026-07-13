from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.usage_log import UsageLog
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsSummary,
    AnalyticsTotals,
    DailyMessageCount,
    DailyTokenUsage,
    ToolUsageCount,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_DAYS = 90


def _utc_date_col(column: ColumnElement[datetime], dialect: str) -> ColumnElement[str]:
    """A 'YYYY-MM-DD' UTC-date expression for grouping, per backend. Timestamps
    are always stored as UTC; Postgres timestamptz must be pinned to UTC before
    truncation so the result doesn't drift with the session timezone, while
    SQLite stores naive UTC strings that strftime reads directly."""
    if dialect == "postgresql":
        return func.to_char(func.timezone("UTC", column), "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", column)


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    days: int = Query(default=14, ge=1, le=MAX_DAYS),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsSummary:
    dialect = db.get_bind().dialect.name
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    earliest_dt = datetime.combine(
        today - timedelta(days=days - 1), datetime.min.time(), tzinfo=timezone.utc
    )

    # Aggregate per-day in the database (GROUP BY the UTC date) rather than
    # streaming every row into Python and counting there, so memory stays flat
    # regardless of how much history a user has.
    message_day = _utc_date_col(Message.created_at, dialect)
    message_counts_stmt = (
        select(message_day, func.count())
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == current_user.id,
            Message.role == MessageRole.user,
            Message.created_at >= earliest_dt,
        )
        .group_by(message_day)
    )
    message_counts: dict[str, int] = {
        day: count for day, count in (await db.execute(message_counts_stmt)).all()
    }

    usage_day = _utc_date_col(UsageLog.created_at, dialect)
    usage_stmt = (
        select(
            usage_day,
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0),
        )
        .where(UsageLog.user_id == current_user.id, UsageLog.created_at >= earliest_dt)
        .group_by(usage_day)
    )
    token_buckets: dict[str, dict[str, int]] = {k: {"prompt_tokens": 0, "completion_tokens": 0} for k in day_keys}
    for day, prompt_tokens, completion_tokens in (await db.execute(usage_stmt)).all():
        if day in token_buckets:
            token_buckets[day] = {
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
            }

    tool_usage_stmt = (
        select(Message.tool_name, func.count())
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == current_user.id,
            Message.role == MessageRole.tool,
            Message.tool_name.is_not(None),
        )
        .group_by(Message.tool_name)
        .order_by(func.count().desc())
    )
    tool_rows = (await db.execute(tool_usage_stmt)).all()

    total_conversations = (
        await db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == current_user.id)
        )
    ) or 0

    total_messages = (
        await db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == current_user.id, Message.role == MessageRole.user)
        )
    ) or 0

    total_prompt_tokens, total_completion_tokens = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
            ).where(UsageLog.user_id == current_user.id)
        )
    ).one()

    return AnalyticsSummary(
        messages_per_day=[DailyMessageCount(date=k, count=message_counts.get(k, 0)) for k in day_keys],
        tokens_per_day=[
            DailyTokenUsage(date=k, prompt_tokens=v["prompt_tokens"], completion_tokens=v["completion_tokens"])
            for k, v in token_buckets.items()
        ],
        tool_usage=[ToolUsageCount(tool_name=name, count=count) for name, count in tool_rows],
        totals=AnalyticsTotals(
            conversations=total_conversations,
            messages=total_messages,
            prompt_tokens=int(total_prompt_tokens),
            completion_tokens=int(total_completion_tokens),
        ),
    )
