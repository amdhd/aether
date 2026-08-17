"""Password change, password reset, and email verification.

The happy paths matter less here than the properties around them: that a reset
link is single-use and expiring, that it is never stored in a replayable form,
that these flows end other sessions, and that /forgot-password cannot be used to
discover which addresses have accounts.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User
from app.services import email_tokens
from tests.conftest import IS_SQLITE, TestingSessionLocal

PASSWORD = "supersecret123"
NEW_PASSWORD = "an-entirely-new-password"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Capture outbound mail as (to, name, token) instead of sending it."""
    captured: list[tuple[str, str, str]] = []

    async def fake_reset(to: str, name: str, token: str) -> bool:
        captured.append((to, name, token))
        return True

    async def fake_verify(to: str, name: str, token: str) -> bool:
        captured.append((to, name, token))
        return True

    monkeypatch.setattr("app.api.routes.auth.email.send_password_reset", fake_reset)
    monkeypatch.setattr("app.api.routes.auth.email.send_email_verification", fake_verify)
    return captured


async def _register_and_login(client: AsyncClient, email: str = "user@example.com") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "User", "password": PASSWORD},
    )
    resp = await client.post(
        "/api/v1/auth/login", data={"username": email, "password": PASSWORD}
    )
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- change password ---------------------------------------------------------


async def test_change_password_requires_the_current_one(client: AsyncClient, sent: list) -> None:
    """An access token alone must not be enough. Otherwise anyone who borrows a
    live session can lock the real owner out of their own account."""
    token = await _register_and_login(client)

    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not-the-password", "new_password": NEW_PASSWORD},
        headers=_auth(token),
    )
    assert resp.status_code == 400

    # ...and the password really is unchanged.
    still_works = await client.post(
        "/api/v1/auth/login", data={"username": "user@example.com", "password": PASSWORD}
    )
    assert still_works.status_code == 200


async def test_change_password_ends_other_sessions_but_not_this_one(
    client: AsyncClient, sent: list
) -> None:
    """Evicting other sessions is the point of a password change; logging the
    person out of the tab they are standing in is not."""
    first = await _register_and_login(client)
    # A second, independent session for the same account.
    second_login = await client.post(
        "/api/v1/auth/login", data={"username": "user@example.com", "password": PASSWORD}
    )
    second = second_login.json()["access_token"]

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=_auth(second),
    )
    assert changed.status_code == 200
    reissued = changed.json()["access_token"]

    # The other session is dead (token_version moved).
    assert (await client.get("/api/v1/auth/me", headers=_auth(first))).status_code == 401
    # The caller's freshly issued token still works.
    assert (await client.get("/api/v1/auth/me", headers=_auth(reissued))).status_code == 200
    # The old password no longer signs in; the new one does.
    old = await client.post(
        "/api/v1/auth/login", data={"username": "user@example.com", "password": PASSWORD}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login", data={"username": "user@example.com", "password": NEW_PASSWORD}
    )
    assert new.status_code == 200


async def test_change_password_enforces_the_bcrypt_byte_ceiling(
    client: AsyncClient, sent: list
) -> None:
    """The registration endpoint already caps this; a second entry point that
    didn't would quietly set the real policy (and 500 on the hash call)."""
    token = await _register_and_login(client)
    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "😀" * 40},
        headers=_auth(token),
    )
    assert resp.status_code == 422


# --- forgot / reset ----------------------------------------------------------


async def test_forgot_password_does_not_reveal_whether_the_account_exists(
    client: AsyncClient, sent: list
) -> None:
    """The endpoint is unauthenticated, so a different answer for a real address
    turns it into an account enumerator."""
    await _register_and_login(client)
    # Registration already mailed a verification link; only what the two calls
    # below send is under test.
    before = len(sent)

    real = await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    missing = await client.post("/api/v1/auth/forgot-password", json={"email": "ghost@example.com"})

    assert real.status_code == missing.status_code == 202
    assert real.json() == missing.json()
    # Only the real address actually gets mail.
    assert [to for to, _, _ in sent[before:]] == ["user@example.com"]


async def test_reset_password_signs_in_with_the_new_password_and_ends_sessions(
    client: AsyncClient, sent: list
) -> None:
    live = await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, token = sent[-1]

    resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 204

    # Resetting is what you do when someone else may have access, so sessions go.
    assert (await client.get("/api/v1/auth/me", headers=_auth(live))).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", data={"username": "user@example.com", "password": NEW_PASSWORD}
        )
    ).status_code == 200


async def test_reset_token_is_single_use(client: AsyncClient, sent: list) -> None:
    await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, token = sent[-1]

    first = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert first.status_code == 204

    replay = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "another-password-99"}
    )
    assert replay.status_code == 400


async def test_issuing_a_new_reset_link_kills_the_previous_one(
    client: AsyncClient, sent: list
) -> None:
    """A link that leaks from an older inbox message must already be dead."""
    await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, first_token = sent[-1]
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, second_token = sent[-1]
    assert first_token != second_token

    stale = await client.post(
        "/api/v1/auth/reset-password", json={"token": first_token, "new_password": NEW_PASSWORD}
    )
    assert stale.status_code == 400
    fresh = await client.post(
        "/api/v1/auth/reset-password", json={"token": second_token, "new_password": NEW_PASSWORD}
    )
    assert fresh.status_code == 204


async def test_expired_reset_token_is_rejected(client: AsyncClient, sent: list) -> None:
    await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, token = sent[-1]

    async with TestingSessionLocal() as db:
        record = await db.scalar(
            select(EmailToken).where(EmailToken.purpose == EmailTokenPurpose.password_reset)
        )
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 400


async def test_the_plaintext_token_is_never_stored(client: AsyncClient, sent: list) -> None:
    """A database dump must not yield anything replayable."""
    await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, token = sent[-1]

    async with TestingSessionLocal() as db:
        record = await db.scalar(
            select(EmailToken).where(EmailToken.purpose == EmailTokenPurpose.password_reset)
        )
        assert record.token_hash != token
        assert record.token_hash == hashlib.sha256(token.encode()).hexdigest()


async def test_a_verification_token_cannot_be_redeemed_as_a_password_reset(
    client: AsyncClient, sent: list
) -> None:
    """Both purposes share one table, which is exactly the arrangement that
    invites confusing one for the other."""
    await _register_and_login(client)
    verification_token = sent[0][2]  # issued by registration

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": verification_token, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 400


async def test_forgot_password_is_rate_limited(client: AsyncClient, sent: list) -> None:
    payload = {"email": "ghost@example.com"}
    for _ in range(settings.AUTH_RATE_LIMIT_PER_MINUTE):
        assert (await client.post("/api/v1/auth/forgot-password", json=payload)).status_code == 202
    limited = await client.post("/api/v1/auth/forgot-password", json=payload)
    assert limited.status_code == 429


# --- email verification ------------------------------------------------------


async def test_registration_sends_a_verification_link_and_starts_unverified(
    client: AsyncClient, sent: list
) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "fresh@example.com", "name": "Fresh", "password": PASSWORD},
    )
    assert resp.status_code == 201
    assert resp.json()["email_verified"] is False
    assert [to for to, _, _ in sent] == ["fresh@example.com"]


async def test_confirming_the_link_verifies_the_address(client: AsyncClient, sent: list) -> None:
    token = await _register_and_login(client)
    verification_token = sent[0][2]

    resp = await client.post(
        "/api/v1/auth/verify-email/confirm", json={"token": verification_token}
    )
    assert resp.status_code == 204

    me = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.json()["email_verified"] is True


async def test_confirming_does_not_require_being_signed_in(client: AsyncClient, sent: list) -> None:
    """The link is opened from a mail client, which may not be a browser the
    user is signed into."""
    await _register_and_login(client)
    verification_token = sent[0][2]

    # No Authorization header at all.
    resp = await client.post(
        "/api/v1/auth/verify-email/confirm", json={"token": verification_token}
    )
    assert resp.status_code == 204


async def test_verification_is_advisory_so_an_unverified_user_can_use_the_app(
    client: AsyncClient, sent: list
) -> None:
    """The chosen policy: nothing is gated on verification, so accounts that
    predate it keep working."""
    token = await _register_and_login(client)
    assert (await client.get("/api/v1/auth/me", headers=_auth(token))).json()["email_verified"] is False
    assert (await client.get("/api/v1/tasks", headers=_auth(token))).status_code == 200
    assert (await client.get("/api/v1/notes", headers=_auth(token))).status_code == 200


async def test_resend_verification_invalidates_the_earlier_link(
    client: AsyncClient, sent: list
) -> None:
    token = await _register_and_login(client)
    first_token = sent[0][2]

    resp = await client.post("/api/v1/auth/verify-email/send", headers=_auth(token))
    assert resp.status_code == 202
    second_token = sent[-1][2]
    assert second_token != first_token

    stale = await client.post("/api/v1/auth/verify-email/confirm", json={"token": first_token})
    assert stale.status_code == 400
    fresh = await client.post("/api/v1/auth/verify-email/confirm", json={"token": second_token})
    assert fresh.status_code == 204


async def test_resend_is_a_no_op_once_verified(client: AsyncClient, sent: list) -> None:
    token = await _register_and_login(client)
    await client.post("/api/v1/auth/verify-email/confirm", json={"token": sent[0][2]})
    before = len(sent)

    resp = await client.post("/api/v1/auth/verify-email/send", headers=_auth(token))
    assert resp.status_code == 202
    assert len(sent) == before, "no second email for an already-confirmed address"


async def test_completing_a_reset_also_verifies_the_address(
    client: AsyncClient, sent: list
) -> None:
    """Holding the reset link proves control of the inbox, which is the same
    thing verification asks for."""
    await _register_and_login(client)
    await client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})
    _, _, reset_token = sent[-1]

    await client.post(
        "/api/v1/auth/reset-password", json={"token": reset_token, "new_password": NEW_PASSWORD}
    )

    relogin = await client.post(
        "/api/v1/auth/login", data={"username": "user@example.com", "password": NEW_PASSWORD}
    )
    me = await client.get("/api/v1/auth/me", headers=_auth(relogin.json()["access_token"]))
    assert me.json()["email_verified"] is True


# --- service-level ------------------------------------------------------------


@pytest.mark.skipif(
    IS_SQLITE,
    reason="StaticPool shares one connection, so overlapping transactions collide "
    "in the driver before the logic runs; the Postgres CI leg is the real test.",
)
async def test_concurrent_redemptions_of_one_token_yield_a_single_winner() -> None:
    """Same lesson as refresh-token rotation: the check and the claim have to be
    one statement, or two callers both pass and both apply the effect."""
    async with TestingSessionLocal() as db:
        user = User(email="race@example.com", name="Race", password_hash="unused")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        issued = await email_tokens.issue(db, user, EmailTokenPurpose.password_reset)
        await db.commit()

    async def redeem() -> bool:
        async with TestingSessionLocal() as db:
            try:
                await email_tokens.redeem(db, issued.token, EmailTokenPurpose.password_reset)
                await db.commit()
                return True
            except email_tokens.EmailTokenError:
                return False

    outcomes = await asyncio.gather(redeem(), redeem())
    assert sum(outcomes) == 1, "both callers redeemed the same token"
