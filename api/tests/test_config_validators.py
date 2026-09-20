"""Startup validators that refuse an unsafe production configuration.

The pattern throughout: a setting whose wrong value is silent at runtime and
expensive in production fails the process at boot instead, where a deploy
catches it.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PRODUCTION = {
    "_env_file": None,
    "ENVIRONMENT": "production",
    "SECRET_KEY": "a-real-secret-for-this-test",
    "ENCRYPTION_KEY": "jNi4_f4xMY-2e7ddrk7jghfP_pblo7NtEYsp3X065Jk=",
}


def test_a_wildcard_origin_is_refused_in_production() -> None:
    """app.main pairs this allowlist with allow_credentials=True.

    The two are only safe together because the list is explicit: a wildcard
    means any site a signed-in user visits can call this API with their cookies
    attached and read the replies. Browsers reject the literal pairing, but
    Starlette treats "*" as "echo the caller's Origin back" — the same hole with
    the browser's check satisfied — so it has to be refused here.
    """
    with pytest.raises(ValidationError):
        Settings(**PRODUCTION, FRONTEND_ORIGIN="*")


def test_a_wildcard_hidden_in_a_list_is_refused_too() -> None:
    with pytest.raises(ValidationError):
        Settings(**PRODUCTION, FRONTEND_ORIGIN="https://aether.example.com,*")


def test_an_empty_origin_is_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(**PRODUCTION, FRONTEND_ORIGIN="")


def test_an_origin_without_a_scheme_is_refused() -> None:
    """The first entry is also the base for links mailed to users, so a
    scheme-less value ships password-reset links that do not resolve."""
    with pytest.raises(ValidationError):
        Settings(**PRODUCTION, FRONTEND_ORIGIN="aether.example.com")


def test_explicit_origins_are_accepted() -> None:
    settings = Settings(
        **PRODUCTION, FRONTEND_ORIGIN="https://aether.example.com,https://www.aether.example.com"
    )
    assert settings.FRONTEND_ORIGIN.startswith("https://aether.example.com")


def test_development_is_left_alone() -> None:
    """Local dev runs on http://localhost and must not need production's rules."""
    assert Settings(_env_file=None, ENVIRONMENT="development", FRONTEND_ORIGIN="*")
