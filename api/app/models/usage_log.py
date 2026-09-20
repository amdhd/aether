from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class UsageLog(Base):
    __tablename__ = "usage_logs"

    # Every chat turn asks the same question before it runs: what has this user
    # spent since the start of the month (app.core.cost_cap), which is
    # `WHERE user_id = ? AND created_at >= ?`. On a user_id-only index Postgres
    # finds the user's rows and then reads all of them to filter by date, so the
    # per-turn cost grows with everything that user has ever done. The analytics
    # day-buckets run the same shape and are served by the same index.
    #
    # No separate user_id index: this one's leftmost column is user_id, so it
    # answers a user_id-only lookup (and the cascade delete) too, and keeping
    # both would mean paying for two index writes per row to answer one query.
    __table_args__ = (Index("ix_usage_logs_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Nullable because not all billable work happens inside a conversation: a
    # note embedding is charged to the user with no chat turn to attribute it to.
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Priced at EMBEDDING_COST_PER_1M_TOKENS rather than the chat rates, so it
    # gets its own column instead of being folded into prompt_tokens (which
    # would overcharge it by more than an order of magnitude).
    embedding_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
