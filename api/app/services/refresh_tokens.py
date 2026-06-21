"""Refresh-token rotation with reuse (theft) detection.

Each login starts a token *family*. Every refresh rotates the token: the
presented token is revoked and a new one is issued in the same family. A valid
refresh token is therefore single-use. If a revoked token is presented again,
the token has leaked and is being replayed, so the entire family is revoked and
the user's `token_version` is bumped (which also invalidates outstanding access
tokens). The legitimate client simply logs in again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.models.refresh_token import RefreshToken
from app.models.user import User


class RefreshError(Exception):
    """Raised when a refresh token is invalid, expired, or replayed."""


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


async def issue_token_pair(db: AsyncSession, user: User, family_id: str | None = None) -> IssuedTokens:
    """Mint an access token plus a new refresh token. Pass `family_id` to keep
    a rotation chain going; omit it to start a fresh family (i.e. a new login)."""
    jti = uuid.uuid4().hex
    family = family_id or uuid.uuid4().hex
    db.add(
        RefreshToken(
            jti=jti, family_id=family, user_id=user.id, revoked=False, expires_at=_expiry()
        )
    )
    await db.commit()
    return IssuedTokens(
        access_token=create_access_token(str(user.id), user.token_version),
        refresh_token=create_refresh_token(str(user.id), user.token_version, jti, family),
    )


async def _revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(
        update(RefreshToken).where(RefreshToken.family_id == family_id).values(revoked=True)
    )


async def rotate_refresh_token(db: AsyncSession, raw_token: str) -> IssuedTokens:
    """Validate and rotate a refresh token, returning a fresh token pair.
    Raises RefreshError on any problem (and revokes the family on replay)."""
    try:
        payload = decode_token(raw_token)
        if payload.get("type") != "refresh":
            raise RefreshError("Not a refresh token")
        jti = payload["jti"]
        family_id = payload["fam"]
        user_id = int(payload["sub"])
        token_version = payload["ver"]
    except (JWTError, KeyError, ValueError, TypeError) as exc:
        raise RefreshError("Invalid refresh token") from exc

    record = await db.scalar(select(RefreshToken).where(RefreshToken.jti == jti))

    # Unknown jti, or a known-but-already-revoked one: treat as replay of a
    # rotated/leaked token and burn the whole family.
    if record is None or record.revoked:
        await _revoke_family(db, family_id)
        await db.commit()
        raise RefreshError("Refresh token reuse detected")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise RefreshError("Refresh token expired")

    user = await db.get(User, user_id)
    if user is None or user.token_version != token_version:
        raise RefreshError("Refresh token no longer valid")

    record.revoked = True
    await db.commit()
    return await issue_token_pair(db, user, family_id=family_id)


async def revoke_all_for_user(db: AsyncSession, user: User) -> None:
    """Revoke every outstanding refresh token for a user (used on logout) and
    bump token_version so existing access tokens stop validating too."""
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    user.token_version += 1
    db.add(user)
    await db.commit()
