"""Issue and redeem the single-use tokens mailed for password reset and email
verification.

Three properties carry the security of both flows:

* **The token is never stored.** Only its SHA-256 digest is, so the database
  holds nothing replayable. The plaintext exists exactly once, in the email.
* **Issuing invalidates the outstanding ones.** Asking for a second reset link
  kills the first, so a link that leaks from an old inbox message is already
  dead by the time anyone finds it.
* **Redemption is atomic.** Marking a token used is a conditional UPDATE, so two
  concurrent redemptions cannot both succeed — the same reason
  `refresh_tokens.rotate_refresh_token` claims its row that way.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User

# A reset link is a credential in someone's inbox, so it lives briefly.
# Verification is not a credential — it grants no access — so it may live long
# enough to survive a user who opens the mail the next morning.
PASSWORD_RESET_TTL = timedelta(hours=1)
EMAIL_VERIFICATION_TTL = timedelta(hours=24)

_TTL_BY_PURPOSE = {
    EmailTokenPurpose.password_reset: PASSWORD_RESET_TTL,
    EmailTokenPurpose.email_verification: EMAIL_VERIFICATION_TTL,
}


class EmailTokenError(Exception):
    """Raised when a token is unknown, expired, already used, or the wrong type."""


@dataclass
class IssuedEmailToken:
    """The plaintext to mail, and the row that will recognise it."""

    token: str
    expires_at: datetime


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue(db: AsyncSession, user: User, purpose: EmailTokenPurpose) -> IssuedEmailToken:
    """Mint a token for `user`, invalidating any outstanding one of the same
    purpose. Caller is responsible for committing."""
    # token_urlsafe(32) is 256 bits — far past guessing, and short enough to
    # survive a mail client wrapping the URL.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + _TTL_BY_PURPOSE[purpose]

    # Retire the previous links for this purpose. Marking them used rather than
    # deleting keeps a later click distinguishable from a forged token.
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
    )
    db.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_digest(token),
            expires_at=expires_at,
        )
    )
    return IssuedEmailToken(token=token, expires_at=expires_at)


async def redeem(db: AsyncSession, token: str, purpose: EmailTokenPurpose) -> User:
    """Consume a token and return the user it belongs to.

    Raises `EmailTokenError` for anything wrong with it. Commits nothing — the
    caller commits the redemption together with whatever it authorised, so a
    failure downstream cannot burn the token without applying its effect.
    """
    record = await db.scalar(select(EmailToken).where(EmailToken.token_hash == _digest(token)))

    # Checking the purpose here (rather than in the lookup) means a verification
    # link can never be redeemed as a password reset even though both live in
    # one table — the confusion that a shared token store invites.
    if record is None or record.purpose != purpose:
        raise EmailTokenError("Unknown token")
    if record.used_at is not None:
        raise EmailTokenError("Token already used")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        # SQLite hands back naive datetimes; we always store UTC.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise EmailTokenError("Token expired")

    # Claim it. Conditional on still being unused so two concurrent redemptions
    # cannot both pass the check above and both apply their effect.
    claimed = await db.execute(
        update(EmailToken)
        .where(EmailToken.id == record.id, EmailToken.used_at.is_(None))
        .values(used_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount == 0:
        raise EmailTokenError("Token already used")

    user = await db.get(User, record.user_id)
    if user is None:
        raise EmailTokenError("User no longer exists")
    return user
