from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.models.google_credential import GoogleCredential
from app.models.user import User

AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Refresh slightly ahead of actual expiry to avoid using a token that expires
# mid-request.
TOKEN_EXPIRY_LEEWAY_SECONDS = 60


def build_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": settings.GOOGLE_OAUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh_access_token(refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_credential(db: AsyncSession, user: User) -> GoogleCredential | None:
    stmt = select(GoogleCredential).where(GoogleCredential.user_id == user.id)
    return (await db.scalars(stmt)).first()


async def upsert_credential(db: AsyncSession, user: User, token_data: dict[str, Any]) -> GoogleCredential:
    expires_in = token_data.get("expires_in", 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    refresh_token = token_data.get("refresh_token")

    credential = await get_credential(db, user)
    if credential is None:
        if not refresh_token:
            raise ValueError("Google did not return a refresh token; reconnect and grant offline access.")
        credential = GoogleCredential(
            user_id=user.id,
            access_token_encrypted=encrypt_token(token_data["access_token"]),
            refresh_token_encrypted=encrypt_token(refresh_token),
            token_expiry=expiry,
            scope=token_data.get("scope", settings.GOOGLE_OAUTH_SCOPES),
        )
        db.add(credential)
    else:
        credential.access_token_encrypted = encrypt_token(token_data["access_token"])
        credential.token_expiry = expiry
        if refresh_token:
            credential.refresh_token_encrypted = encrypt_token(refresh_token)
        if token_data.get("scope"):
            credential.scope = token_data["scope"]

    await db.commit()
    await db.refresh(credential)
    return credential


async def revoke_credential(db: AsyncSession, user: User) -> None:
    """Disconnect Google Calendar: revoke the grant at Google, then delete the
    stored credential. Revoking the refresh token invalidates the whole grant
    (access + refresh) server-side, so a merely-local delete can't leave a live
    token behind. Google/network errors are swallowed — a transient failure
    there must not block the user from disconnecting locally."""
    credential = await get_credential(db, user)
    if credential is None:
        return

    try:
        token = decrypt_token(credential.refresh_token_encrypted)
    except Exception:
        token = None

    if token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            pass

    await db.delete(credential)
    await db.commit()


async def get_valid_access_token(db: AsyncSession, user: User) -> str | None:
    """Returns a usable access token for the user's Google account,
    refreshing it first if it's expired (or about to expire). Returns None
    if the user hasn't connected Google Calendar, or the refresh fails."""
    credential = await get_credential(db, user)
    if credential is None:
        return None

    # SQLite returns DateTime(timezone=True) values as naive (we always store
    # UTC), while Postgres/asyncpg returns them tz-aware. Normalize before comparing.
    expiry = credential.token_expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    if expiry > datetime.now(timezone.utc) + timedelta(seconds=TOKEN_EXPIRY_LEEWAY_SECONDS):
        return decrypt_token(credential.access_token_encrypted)

    try:
        token_data = await _refresh_access_token(decrypt_token(credential.refresh_token_encrypted))
    except httpx.HTTPError:
        return None

    credential.access_token_encrypted = encrypt_token(token_data["access_token"])
    credential.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 3600))
    await db.commit()
    return decrypt_token(credential.access_token_encrypted)
