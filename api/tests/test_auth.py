import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.config import settings
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import refresh_tokens
from tests.conftest import IS_SQLITE, TestingSessionLocal

COOKIE = settings.REFRESH_COOKIE_NAME
# /auth/refresh is the one cookie-authenticated endpoint, so it demands a header
# a cross-site caller cannot set. Every legitimate call carries it.
CSRF = {"X-Requested-With": "XMLHttpRequest"}


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
    refresh_resp = await client.post("/api/v1/auth/refresh", headers=CSRF)
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
    first = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert first.status_code == 200
    rotated = client.cookies.get(COOKIE)
    assert rotated != stolen

    # Replaying the now-revoked original token is treated as theft -> 401.
    client.cookies.clear()
    replay = await client.post("/api/v1/auth/refresh", cookies={COOKIE: stolen}, headers=CSRF)
    assert replay.status_code == 401

    # ...and the whole family is burned, so the legitimately rotated token dies too.
    client.cookies.clear()
    after = await client.post("/api/v1/auth/refresh", cookies={COOKIE: rotated}, headers=CSRF)
    assert after.status_code == 401


async def _issue_token_for(email: str) -> str:
    async with TestingSessionLocal() as db:
        user = User(email=email, name="Race", password_hash="unused")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return (await refresh_tokens.issue_token_pair(db, user)).refresh_token


async def test_rotation_rejects_a_token_revoked_after_it_was_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotation has to be atomic, not merely ordered.

    Reading the row, testing `revoked`, then writing it leaves a window: two
    requests presenting the same token both see it live and both get a successor
    — which is the replay the family-burn exists to catch, so the race disarmed
    the detection rather than just duplicating work. The revoke is now conditional
    on the row still being unrevoked.

    This drives the window directly instead of racing for it: the row is revoked
    part-way through the rotation, at the point a competing caller's write would
    land. The conditional UPDATE must then match nothing and reject.
    """
    original = await _issue_token_for("midrotation@example.com")

    async with TestingSessionLocal() as db:
        real_get = db.get

        async def revoke_then_get(*args, **kwargs):
            # Fires after rotate_refresh_token has read the row and judged it
            # live, and before it writes — exactly where the loser of a real
            # race finds itself.
            await db.execute(update(RefreshToken).values(revoked=True))
            return await real_get(*args, **kwargs)

        monkeypatch.setattr(db, "get", revoke_then_get)

        with pytest.raises(refresh_tokens.RefreshError):
            await refresh_tokens.rotate_refresh_token(db, original)


@pytest.mark.skipif(
    IS_SQLITE,
    reason="StaticPool shares one connection, so overlapping transactions collide "
    "in the driver before the logic runs; the Postgres CI leg is the real test.",
)
async def test_concurrent_rotations_of_one_token_yield_a_single_winner() -> None:
    """The same guarantee under genuine concurrency."""
    original = await _issue_token_for("race@example.com")

    async def rotate() -> bool:
        async with TestingSessionLocal() as db:
            try:
                await refresh_tokens.rotate_refresh_token(db, original)
                return True
            except refresh_tokens.RefreshError:
                return False

    outcomes = await asyncio.gather(rotate(), rotate())
    assert sum(outcomes) == 1, "both callers rotated the same token"


async def test_reset_password_signs_out_existing_sessions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resetting a password is the recovery path for a compromised account, so it
    has to end the sessions the old password bought. A stolen refresh token that
    survives the reset would rotate itself indefinitely."""
    from scripts import reset_password

    await client.post(
        "/api/v1/auth/register",
        json={"email": "reset@example.com", "name": "Reset", "password": "supersecret123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "reset@example.com", "password": "supersecret123"},
    )
    stolen_access = login.json()["access_token"]
    stolen_refresh = client.cookies.get(COOKIE)

    monkeypatch.setattr(reset_password, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(reset_password.getpass, "getpass", lambda _prompt: "brand-new-password")
    assert await reset_password._reset("reset@example.com") == 0

    # The access token minted before the reset must stop validating (token_version).
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {stolen_access}"}
    )
    assert me.status_code == 401

    # ...and so must the refresh token, so it can't mint a fresh one.
    client.cookies.clear()
    replay = await client.post("/api/v1/auth/refresh", cookies={COOKIE: stolen_refresh})
    assert replay.status_code == 401

    # The new password works.
    relogin = await client.post(
        "/api/v1/auth/login",
        data={"username": "reset@example.com", "password": "brand-new-password"},
    )
    assert relogin.status_code == 200


async def test_refresh_without_cookie(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert resp.status_code == 401


async def test_refresh_with_invalid_cookie(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/refresh", cookies={COOKIE: "not-a-real-token"}, headers=CSRF
    )
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


async def test_refresh_rejects_a_request_without_the_csrf_header(client: AsyncClient) -> None:
    """The refresh endpoint is the only one that authenticates with a cookie, so
    it is the only one a cross-site page can invoke with the victim's credentials
    simply by making the browser send them. Production issues that cookie with
    SameSite=none — the SPA and the API are on different origins — so SameSite
    cannot be what stops this."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "csrf@example.com", "name": "Csrf", "password": "supersecret123"},
    )
    await client.post(
        "/api/v1/auth/login",
        data={"username": "csrf@example.com", "password": "supersecret123"},
    )
    cookie = client.cookies.get(COOKIE)

    # A forged cross-site POST: the cookie is valid and present, the header is
    # not, because a form or a no-cors fetch cannot set one.
    forged = await client.post("/api/v1/auth/refresh")
    assert forged.status_code == 403

    # The cookie must survive the rejection — a forged request that consumed the
    # victim's token would burn the family on their next legitimate refresh,
    # turning a blocked attack into a forced logout.
    legitimate = await client.post(
        "/api/v1/auth/refresh", cookies={COOKIE: cookie}, headers=CSRF
    )
    assert legitimate.status_code == 200


async def test_refresh_rate_limit(client: AsyncClient) -> None:
    """/refresh is unauthenticated, reachable by anyone, and does real work on
    every call — a JWT verify, a lookup, and on the replay path a family-wide
    UPDATE and commit. It needs the same per-IP ceiling as login."""
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post("/api/v1/auth/refresh", headers=CSRF)
        assert resp.status_code == 401

    limited = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


async def test_refresh_rate_limit_meters_requests_that_fail_the_csrf_check(
    client: AsyncClient,
) -> None:
    """The rate limit is solved before the CSRF guard on purpose. If the guard
    ran first, omitting the header would be a free way to hammer the endpoint."""
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 403

    limited = await client.post("/api/v1/auth/refresh")
    assert limited.status_code == 429


async def test_refresh_bucket_is_separate_from_login(client: AsyncClient) -> None:
    """Exhausting one auth endpoint must not lock a user out of the other."""
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 429

    login = await client.post(
        "/api/v1/auth/login", data={"username": "nobody@example.com", "password": "wrong"}
    )
    assert login.status_code == 401
