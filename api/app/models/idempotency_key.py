from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class IdempotencyKey(Base):
    """A client-supplied key already spent on a write.

    Only the claim is stored, not the response. The endpoint this guards streams
    SSE for the length of a model turn, so there is no response body to keep and
    replay — the duplicate has to be *refused*, and refusing only needs to know
    the key was used. That also keeps rows small and free of message content.

    Scoped per user so one account cannot consume or probe another's keys, and
    per endpoint so a client reusing one key across different writes does not
    have the second silently swallowed as a duplicate of the first.
    """

    __tablename__ = "idempotency_keys"

    __table_args__ = (
        # The claim: whoever inserts this row first owns the write, and the
        # database decides who that is. Nothing here is a check-then-act.
        UniqueConstraint("user_id", "scope", "key", name="uq_idempotency_user_scope_key"),
        # Rows accumulate at roughly one per message sent — the same order as
        # `messages` itself, so this adds no new growth class — but nothing
        # reads them after their window closes. This index is what makes
        # `DELETE FROM idempotency_keys WHERE created_at < ?` cheap when a
        # deployment wants to prune them.
        Index("ix_idempotency_keys_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Which write this key was spent on, e.g. "conversations.send_message".
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
