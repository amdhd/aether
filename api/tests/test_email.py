"""The outbound-email layer: link construction, the logging fallback, and the
refusal to send credentials over an unencrypted transport."""

import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services import email


def test_links_point_at_the_first_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """FRONTEND_ORIGIN doubles as a comma-separated CORS allowlist, but a link in
    an email has to resolve to one canonical site."""
    monkeypatch.setattr(settings, "FRONTEND_ORIGIN", "https://app.example.com,https://www.example.com")
    assert email._link("/reset-password", "tok") == "https://app.example.com/reset-password?token=tok"


def test_links_tolerate_a_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "FRONTEND_ORIGIN", "https://app.example.com/")
    assert email._link("/verify-email", "tok") == "https://app.example.com/verify-email?token=tok"


async def test_without_smtp_configured_mail_is_logged_not_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The default backend keeps both flows exercisable in dev and CI with no
    mail server. The link must appear, since that is the whole point."""
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(logging.getLogger("app"), "propagate", True)
    with caplog.at_level("INFO", logger="app.services.email"):
        sent = await email.send_password_reset("someone@example.com", "Someone", "tok123")
    assert sent is True
    assert "tok123" in caplog.text


async def test_a_provider_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mail outage must not become a 500 on /forgot-password — that would both
    break the flow and hint at which addresses exist."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")

    async def boom(_message: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(email, "_send_smtp", boom)
    assert await email.send_email("someone@example.com", "Subject", "Body") is False


async def test_a_failure_does_not_log_the_recipient(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Logs land in CloudWatch; the address is exactly what this flow is trying
    not to disclose."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")

    async def boom(_message: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(email, "_send_smtp", boom)
    monkeypatch.setattr(logging.getLogger("app"), "propagate", True)
    with caplog.at_level("WARNING", logger="app.services.email"):
        await email.send_email("secret-user@example.com", "Subject", "Body")
    assert "secret-user@example.com" not in caplog.text


def test_smtp_without_tls_is_refused_at_startup() -> None:
    """SMTP_USERNAME/PASSWORD travel inside the session, so an unencrypted
    transport hands the mail credentials to the network."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            SMTP_HOST="smtp.example.com",
            SMTP_STARTTLS=False,
            SMTP_USE_TLS=False,
        )


def test_the_two_tls_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            SMTP_HOST="smtp.example.com",
            SMTP_STARTTLS=True,
            SMTP_USE_TLS=True,
        )


def test_smtp_settings_accept_each_tls_mode_on_its_own() -> None:
    assert Settings(_env_file=None, SMTP_HOST="s", SMTP_STARTTLS=True, SMTP_USE_TLS=False)
    assert Settings(_env_file=None, SMTP_HOST="s", SMTP_STARTTLS=False, SMTP_USE_TLS=True)
