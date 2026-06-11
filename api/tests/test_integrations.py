from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_oauth_state_token
from app.services import google_oauth


@pytest.fixture(autouse=True)
def _google_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/google/callback")


async def test_google_status_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/google/status")
    assert resp.status_code == 401


async def test_google_status_not_connected(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.get("/api/v1/integrations/google/status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}


async def test_google_connect_not_configured(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")

    resp = await client.get("/api/v1/integrations/google/connect", headers=auth_headers)
    assert resp.status_code == 503


async def test_google_connect_returns_authorization_url(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/integrations/google/connect", headers=auth_headers)
    assert resp.status_code == 200
    url = resp.json()["authorization_url"]

    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["test-client-id"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert "state" in qs


async def test_google_callback_invalid_state_redirects_with_error(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "abc", "state": "not-a-real-token"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "google=error" in resp.headers["location"]


async def test_google_callback_success_stores_credential(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    me = await client.get("/api/v1/conversations", headers=auth_headers)
    assert me.status_code == 200

    # Decode the user id from the access token used in auth_headers.
    from app.core.security import decode_token

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = int(decode_token(token)["sub"])
    state = create_oauth_state_token(user_id)

    async def fake_exchange_code(code: str) -> dict:
        assert code == "auth-code"
        return {
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "expires_in": 3600,
            "scope": settings.GOOGLE_OAUTH_SCOPES,
        }

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange_code)

    resp = await client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "google=connected" in resp.headers["location"]

    status_resp = await client.get("/api/v1/integrations/google/status", headers=auth_headers)
    assert status_resp.json() == {"connected": True}


async def test_google_disconnect(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.security import decode_token

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = int(decode_token(token)["sub"])
    state = create_oauth_state_token(user_id)

    async def fake_exchange_code(code: str) -> dict:
        return {
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "expires_in": 3600,
            "scope": settings.GOOGLE_OAUTH_SCOPES,
        }

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange_code)
    await client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    resp = await client.delete("/api/v1/integrations/google/disconnect", headers=auth_headers)
    assert resp.status_code == 204

    status_resp = await client.get("/api/v1/integrations/google/status", headers=auth_headers)
    assert status_resp.json() == {"connected": False}


async def test_get_valid_access_token_refreshes_when_expired(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.core.security import decode_token
    from app.models.google_credential import GoogleCredential
    from tests.conftest import TestingSessionLocal

    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = int(decode_token(token)["sub"])
    state = create_oauth_state_token(user_id)

    async def fake_exchange_code(code: str) -> dict:
        return {
            "access_token": "access-old",
            "refresh_token": "refresh-456",
            "expires_in": 3600,
            "scope": settings.GOOGLE_OAUTH_SCOPES,
        }

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange_code)
    await client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    # Force the stored token to look expired.
    async with TestingSessionLocal() as session:
        from sqlalchemy import select

        credential = (
            await session.scalars(select(GoogleCredential).where(GoogleCredential.user_id == user_id))
        ).one()
        credential.token_expiry = datetime.now(timezone.utc) - timedelta(seconds=10)
        await session.commit()

    async def fake_refresh(refresh_token: str) -> dict:
        assert refresh_token == "refresh-456"
        return {"access_token": "access-new", "expires_in": 3600}

    monkeypatch.setattr(google_oauth, "_refresh_access_token", fake_refresh)

    async with TestingSessionLocal() as session:
        from app.models.user import User

        user = await session.get(User, user_id)
        access_token = await google_oauth.get_valid_access_token(session, user)

    assert access_token == "access-new"
