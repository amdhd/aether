from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


def _to_utc_date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    days: int = Query(default=14, ge=1, le=MAX_DAYS),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsSummary:
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    earliest_day = day_keys[0]

    message_dates_stmt = (
        select(Message.created_at)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == current_user.id, Message.role == MessageRole.user)
    )
    message_counts: Counter[str] = Counter()
    for created_at in (await db.scalars(message_dates_stmt)).all():
        key = _to_utc_date(created_at)
        if key >= earliest_day:
            message_counts[key] += 1

    usage_stmt = select(UsageLog.created_at, UsageLog.prompt_tokens, UsageLog.completion_tokens).where(
        UsageLog.user_id == current_user.id
    )
    token_buckets: dict[str, dict[str, int]] = {k: {"prompt_tokens": 0, "completion_tokens": 0} for k in day_keys}
    for created_at, prompt_tokens, completion_tokens in (await db.execute(usage_stmt)).all():
        key = _to_utc_date(created_at)
        if key in token_buckets:
            token_buckets[key]["prompt_tokens"] += prompt_tokens
            token_buckets[key]["completion_tokens"] += completion_tokens

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
