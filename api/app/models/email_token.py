import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class EmailTokenPurpose(str, enum.Enum):
    password_reset = "password_reset"
    email_verification = "email_verification"


class EmailToken(Base):
    """A single-use, expiring token delivered to a user's inbox.

    Possession of the emailed value is the entire proof, so the value itself is
    never stored: only its SHA-256 digest is, and a leaked database therefore
    yields nothing that can be replayed. The digest is what we look up by, which
    also means the lookup is a plain indexed equality check rather than a scan.

    SHA-256 rather than bcrypt here on purpose. Adaptive hashing exists to make
    guessing a low-entropy human-chosen secret expensive; these tokens carry 256
    bits from `secrets`, so there is nothing to guess and the slow hash would
    only add latency to every click.
    """

    __tablename__ = "email_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    purpose: Mapped[EmailTokenPurpose] = mapped_column(
        Enum(EmailTokenPurpose, native_enum=False, length=20), nullable=False
    )
    # Unique so a digest collision can never silently authorise the wrong row,
    # and indexed because it is the only column we ever look a token up by.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set when redeemed. Kept (rather than deleting the row) so a second click on
    # the same link is distinguishable from a link that never existed.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="email_tokens")
