from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
from jose import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def _create_token(
    subject: str,
    token_version: int,
    expires_delta: timedelta,
    token_type: Literal["access", "refresh"],
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    to_encode: dict[str, Any] = {
        "sub": subject,
        "ver": token_version,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(subject: str, token_version: int) -> str:
    return _create_token(
        subject, token_version, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "access"
    )


def create_refresh_token(subject: str, token_version: int, jti: str, family_id: str) -> str:
    """A refresh token carries a unique `jti` and a `fam` (family id) so the
    server can rotate it on each use and detect replay of a revoked token."""
    return _create_token(
        subject,
        token_version,
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
        extra_claims={"jti": jti, "fam": family_id},
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def create_oauth_state_token(user_id: int) -> str:
    """Short-lived, signed token used as the OAuth `state` param. Binds the
    callback (an unauthenticated browser redirect) back to the user who
    initiated the flow and protects against CSRF, since it can't be forged
    without SECRET_KEY."""
    now = datetime.now(timezone.utc)
    to_encode: dict[str, Any] = {
        "sub": str(user_id),
        "type": "oauth_state",
        "iat": now,
        "exp": now + timedelta(minutes=settings.OAUTH_STATE_EXPIRE_MINUTES),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_oauth_state_token(token: str) -> int:
    """Returns the user id encoded in an OAuth state token, or raises
    jose.JWTError / ValueError if it's invalid, expired, or the wrong type."""
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("type") != "oauth_state":
        raise ValueError("Invalid state token type")
    return int(payload["sub"])
