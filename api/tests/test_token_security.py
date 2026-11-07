"""JWT verification contracts preserved across the PyJWT migration."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, decode_token, verify_oauth_state_token


def test_access_token_round_trip():
    payload = decode_token(create_access_token("42", 3), "access")
    assert payload["sub"] == "42"
    assert payload["ver"] == 3
    assert payload["type"] == "access"


@pytest.mark.parametrize("claims", [
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
    {"nbf": datetime.now(timezone.utc) + timedelta(minutes=5)},
])
def test_invalid_token_dates_are_rejected(claims):
    token = jwt.encode({"sub": "42", **claims}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, "access")


def test_unsigned_token_is_rejected():
    token = jwt.encode({"sub": "42"}, key="", algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, "access")


def test_wrong_signature_is_rejected():
    token = jwt.encode({"sub": "42"}, "a-different-secret-with-at-least-32-bytes", algorithm=settings.ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, "access")


def test_access_token_cannot_be_used_as_oauth_state():
    with pytest.raises(ValueError, match="Invalid state token type"):
        verify_oauth_state_token(create_access_token("42", 0))


# --- purpose, issuer and required claims --------------------------------------

from app.core.security import (  # noqa: E402
    TOKEN_ISSUER,
    create_oauth_state_token,
    create_refresh_token,
)


def _signed(**claims):
    now = datetime.now(timezone.utc)
    base = {"sub": "42", "ver": 0, "type": "access", "aud": "aether:access", "iss": TOKEN_ISSUER,
            "iat": now, "exp": now + timedelta(minutes=5)}
    base.update(claims)
    return jwt.encode({k: v for k, v in base.items() if v is not None}, settings.SECRET_KEY,
                      algorithm=settings.ALGORITHM)


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "type", "aud", "iss"])
def test_tokens_missing_a_required_claim_are_rejected(missing):
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(_signed(**{missing: None}), "access")


def test_a_token_from_another_issuer_is_rejected():
    with pytest.raises(jwt.InvalidIssuerError):
        decode_token(_signed(iss="someone-else"), "access")


def test_a_refresh_token_is_not_an_access_token():
    refresh = create_refresh_token("42", 0, jti="j", family_id="f")
    with pytest.raises(jwt.InvalidAudienceError):
        decode_token(refresh, "access")
    assert decode_token(refresh, "refresh")["jti"] == "j"


def test_an_access_token_is_not_a_refresh_token():
    with pytest.raises(jwt.InvalidAudienceError):
        decode_token(create_access_token("42", 0), "refresh")


def test_oauth_state_is_not_a_session_token():
    with pytest.raises(jwt.InvalidAudienceError):
        decode_token(create_oauth_state_token(42), "access")


def test_audience_and_type_must_agree():
    # Right audience, wrong type: still refused.
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(_signed(type="refresh"), "access")


def test_tokens_from_before_audiences_existed_are_rejected():
    legacy = jwt.encode({"sub": "42", "ver": 0, "type": "access", "iat": datetime.now(timezone.utc),
                         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                        settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(legacy, "access")


async def test_an_api_request_with_a_refresh_token_is_unauthorised(client):
    refresh = create_refresh_token("1", 0, jti="j", family_id="f")
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401


# --- encryption key rotation ---------------------------------------------------

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

from app.core import crypto  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()

    def use(value):
        monkeypatch.setattr(settings, "ENCRYPTION_KEY", value)
        crypto._fernet.cache_clear()

    yield old, new, use
    crypto._fernet.cache_clear()


def test_rotation_keeps_old_ciphertext_readable_and_reencrypts(keys):
    old, new, use = keys
    use(old)
    stored = crypto.encrypt_token("google-refresh-token")

    use(f"{new}, {old}")
    assert crypto.decrypt_token(stored) == "google-refresh-token"
    rotated = crypto.reencrypt_token(stored)
    assert crypto.decrypt_token(rotated) == "google-refresh-token"

    use(new)  # old key retired
    assert crypto.decrypt_token(rotated) == "google-refresh-token"
    with pytest.raises(InvalidToken):
        crypto.decrypt_token(stored)


def test_new_values_are_encrypted_with_the_first_key(keys):
    old, new, use = keys
    use(f"{new},{old}")
    ciphertext = crypto.encrypt_token("v")
    assert Fernet(new.encode()).decrypt(ciphertext.encode()) == b"v"
