"""JWT verification contracts preserved across the PyJWT migration."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, decode_token, verify_oauth_state_token


def test_access_token_round_trip():
    payload = decode_token(create_access_token("42", 3))
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
        decode_token(token)


def test_unsigned_token_is_rejected():
    token = jwt.encode({"sub": "42"}, key="", algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)


def test_wrong_signature_is_rejected():
    token = jwt.encode({"sub": "42"}, "a-different-secret-with-at-least-32-bytes", algorithm=settings.ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)


def test_access_token_cannot_be_used_as_oauth_state():
    with pytest.raises(ValueError, match="Invalid state token type"):
        verify_oauth_state_token(create_access_token("42", 0))
