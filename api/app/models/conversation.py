import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.user import User


class Persona(str, enum.Enum):
    productivity_coach = "productivity_coach"
    research_assistant = "research_assistant"
    casual_friend = "casual_friend"
    marketing_coach = "marketing_coach"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New conversation")
    persona: Mapped[Persona] = mapped_column(
        Enum(Persona, native_enum=False, length=20), default=Persona.productivity_coach, nullable=False
    )
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_summarized_until_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # Let the DB-level ON DELETE CASCADE remove messages in one statement
        # instead of the ORM loading and deleting them one-by-one. Relies on FK
        # enforcement, which Postgres does natively and SQLite gets via the
        # PRAGMA set in db.session.enable_sqlite_foreign_keys.
        passive_deletes=True,
        order_by="Message.created_at",
    )
