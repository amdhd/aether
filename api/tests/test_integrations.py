from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_oauth_state_token
from app.models.user import User
from app.services import google_oauth
from tests.conftest import TestingSessionLocal


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


async def test_google_disconnect_revokes_grant_at_google(
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

    # Capture the outbound revoke call instead of hitting Google.
    posted: dict = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, url: str, data: dict | None = None, **kwargs) -> _FakeResp:
            posted["url"] = url
            posted["token"] = (data or {}).get("token")
            return _FakeResp()

    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _FakeAsyncClient)

    resp = await client.delete("/api/v1/integrations/google/disconnect", headers=auth_headers)
    assert resp.status_code == 204

    # The stored refresh token was revoked at Google, not just deleted locally.
    assert posted["url"] == google_oauth.REVOKE_URL
    assert posted["token"] == "refresh-456"

    status_resp = await client.get("/api/v1/integrations/google/status", headers=auth_headers)
    assert status_resp.json() == {"connected": False}


async def test_google_disconnect_when_google_revoke_fails_still_disconnects(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

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

    class _BoomAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_BoomAsyncClient":
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("google down")

    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _BoomAsyncClient)

    # A transient Google failure must not block the local disconnect.
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


async def test_a_concurrent_oauth_callback_does_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """google_credentials.user_id is unique and the read-then-insert is not
    atomic, so two callbacks arriving together — a double-clicked consent
    screen, a retried redirect — both find no row and both insert. The loser
    should end up with the credential the winner wrote, not a 500."""
    token_data = {
        "access_token": "access-123",
        "refresh_token": "refresh-456",
        "expires_in": 3600,
        "scope": settings.GOOGLE_OAUTH_SCOPES,
    }

    async with TestingSessionLocal() as db:
        user = User(email="oauth-race@example.com", name="O", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        # Captured before the losing path rolls back: a rollback expires every
        # instance, and reading an expired attribute under the async engine
        # emits a sync SELECT and raises MissingGreenlet.
        user_id = user.id

        first = await google_oauth.upsert_credential(db, user, token_data)
        assert first is not None
        winner_id = first.id

        # Stand in for losing the race: the existence check misses, so the
        # insert proceeds into the constraint the winner already satisfied.
        real_get = google_oauth.get_credential
        calls = {"n": 0}

        async def miss_once(session, for_user):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await real_get(session, for_user)

        monkeypatch.setattr(google_oauth, "get_credential", miss_once)

        credential = await google_oauth.upsert_credential(db, user, token_data)

        # Read inside the session: the losing path rolls back, which expires
        # every instance, so touching these after the block would lazy-load on
        # a closed session rather than assert anything.
        assert credential is not None
        assert credential.user_id == user_id
        # The winner's row, recovered — not a second row, and not a 500.
        assert credential.id == winner_id
