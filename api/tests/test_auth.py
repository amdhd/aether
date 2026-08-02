import pytest
from httpx import AsyncClient

from app.core.config import settings

COOKIE = settings.REFRESH_COOKIE_NAME


async def test_register_and_login(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "name": "Alice", "password": "supersecret123"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body
    assert "password_hash" not in body

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "alice@example.com", "password": "supersecret123"},
    )
    assert resp.status_code == 200
    tokens = resp.json()
    assert "access_token" in tokens
    assert tokens["token_type"] == "bearer"
    # The refresh token must NOT be exposed in the body; it is an HttpOnly cookie.
    assert "refresh_token" not in tokens
    assert resp.cookies.get(COOKIE)


async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "bob@example.com", "name": "Bob", "password": "supersecret123"}
    resp1 = await client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 400


async def test_login_invalid_credentials(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_full_auth_flow(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "name": "Carol", "password": "supersecret123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "carol@example.com", "password": "supersecret123"},
    )
    access_token = login_resp.json()["access_token"]

    me_resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "carol@example.com"

    # The refresh cookie is sent automatically by the client's cookie jar.
    refresh_resp = await client.post("/api/v1/auth/refresh")
    assert refresh_resp.status_code == 200
    new_access = refresh_resp.json()["access_token"]
    assert new_access

    logout_resp = await client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert logout_resp.status_code == 204

    # access token issued before logout is now invalid (token_version incremented)
    me_resp2 = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me_resp2.status_code == 401


async def test_refresh_rotates_and_detects_reuse(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dave@example.com", "name": "Dave", "password": "supersecret123"},
    )
    await client.post(
        "/api/v1/auth/login",
        data={"username": "dave@example.com", "password": "supersecret123"},
    )
    stolen = client.cookies.get(COOKIE)

    # Legitimate rotation: the presented token is consumed, a new one is issued.
    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    rotated = client.cookies.get(COOKIE)
    assert rotated != stolen

    # Replaying the now-revoked original token is treated as theft -> 401.
    client.cookies.clear()
    replay = await client.post("/api/v1/auth/refresh", cookies={COOKIE: stolen})
    assert replay.status_code == 401

    # ...and the whole family is burned, so the legitimately rotated token dies too.
    client.cookies.clear()
    after = await client.post("/api/v1/auth/refresh", cookies={COOKIE: rotated})
    assert after.status_code == 401


async def test_refresh_without_cookie(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_with_invalid_cookie(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/refresh", cookies={COOKIE: "not-a-real-token"})
    assert resp.status_code == 401


async def test_login_rate_limit(client: AsyncClient) -> None:
    payload = {"username": "nobody@example.com", "password": "wrong"}
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post("/api/v1/auth/login", data=payload)
        assert resp.status_code == 401

    resp = await client.post("/api/v1/auth/login", data=payload)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


async def test_login_unknown_user_runs_dummy_verify(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A login for a non-existent account must still run a bcrypt comparison, so
    # its timing matches a real account with a wrong password (no enumeration).
    calls = {"n": 0}
    monkeypatch.setattr(
        "app.api.routes.auth.fake_verify_password", lambda: calls.__setitem__("n", calls["n"] + 1)
    )

    resp = await client.post(
        "/api/v1/auth/login", data={"username": "ghost@example.com", "password": "whatever"}
    )
    assert resp.status_code == 401
    assert calls["n"] == 1


async def test_auth_rate_limit_ignores_a_spoofed_forwarded_prefix(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy appends to X-Forwarded-For rather than replacing it, so a caller
    can put anything to the left of the entry our edge adds. Reading from the
    left let an attacker mint a fresh bucket per request and brute-force logins
    without limit; only the rightmost hop is ours to trust."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    payload = {"username": "nobody@example.com", "password": "wrong"}
    # What the app receives once the ALB has appended the real peer: the client's
    # own (forged) value first, ours last.
    spoofed = lambda n: {"X-Forwarded-For": f"10.0.0.{n}, 203.0.113.7"}  # noqa: E731

    for n in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post("/api/v1/auth/login", data=payload, headers=spoofed(n))
        assert resp.status_code == 401

    # A brand-new forged prefix must not buy another window — the bucket belongs
    # to 203.0.113.7 either way.
    limited = await client.post("/api/v1/auth/login", data=payload, headers=spoofed(99))
    assert limited.status_code == 429

    # ...while a genuinely different client, as seen by the proxy, still gets one.
    other = await client.post(
        "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "10.0.0.1, 198.51.100.4"}
    )
    assert other.status_code == 401


async def test_auth_rate_limit_uses_forwarded_ip_when_trusted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    payload = {"username": "nobody@example.com", "password": "wrong"}

    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post(
            "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "1.1.1.1"}
        )
        assert resp.status_code == 401
    limited = await client.post(
        "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "1.1.1.1"}
    )
    assert limited.status_code == 429

    # A different forwarded client IP gets its own bucket.
    other = await client.post(
        "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "2.2.2.2"}
    )
    assert other.status_code == 401


async def test_auth_rate_limit_ignores_forwarded_ip_when_untrusted(client: AsyncClient) -> None:
    # TRUST_PROXY_HEADERS is off by default, so a spoofed X-Forwarded-For must
    # not let a caller escape the limit by rotating the header value.
    payload = {"username": "nobody@example.com", "password": "wrong"}
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post(
            "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "1.1.1.1"}
        )
        assert resp.status_code == 401

    resp = await client.post(
        "/api/v1/auth/login", data=payload, headers={"X-Forwarded-For": "9.9.9.9"}
    )
    assert resp.status_code == 429


async def test_register_rejects_a_password_bcrypt_cannot_hash(client: AsyncClient) -> None:
    """bcrypt raises above 72 bytes rather than truncating, so a passphrase from
    a password manager used to take registration out with a 500."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "longpw@example.com", "name": "Long", "password": "A" * 100},
    )
    assert resp.status_code == 422


async def test_register_accepts_a_password_at_the_limit(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "atlimit@example.com", "name": "Limit", "password": "A" * 72},
    )
    assert resp.status_code == 201


async def test_register_counts_password_length_in_bytes(client: AsyncClient) -> None:
    # 40 emoji = 40 characters but 160 bytes: a character-only limit would let
    # this reach bcrypt, which is where it would blow up.
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "emoji@example.com", "name": "Emoji", "password": "😀" * 40},
    )
    assert resp.status_code == 422


async def test_login_with_an_over_long_password_is_rejected_not_a_crash(client: AsyncClient) -> None:
    """Login takes an unbounded form field, so anyone could reach bcrypt's limit
    on an unauthenticated endpoint and turn a wrong password into a 500."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "victim@example.com", "name": "V", "password": "correct-horse"},
    )
    resp = await client.post(
        "/api/v1/auth/login", data={"username": "victim@example.com", "password": "A" * 100}
    )
    assert resp.status_code == 401
