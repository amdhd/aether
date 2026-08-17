"""Outbound transactional email.

Two backends behind one call, mirroring how `app.core.rate_limit` degrades:

* **SMTP** — used when ``SMTP_HOST`` is set. Any provider (SES SMTP, Postmark,
  Mailgun) works, so nothing here is tied to a vendor and the ECS task role
  stays empty.
* **Logging** — the default. Writes the message, link included, to the
  application log so password reset and verification are exercisable in local
  dev and CI with no mail server and no credentials.

Sending is best-effort by design. A provider outage must not turn "reset my
password" into a 500 that also tells the caller whether the account existed, so
`send` reports failure and the routes answer the same way regardless.
"""

from email.message import EmailMessage

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM or "aether@localhost"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


async def _send_smtp(message: EmailMessage) -> None:
    import aiosmtplib

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USERNAME or None,
        password=settings.SMTP_PASSWORD or None,
        # STARTTLS on the submission port (587) is the common shape; use_tls is
        # implicit TLS on 465. Both encrypt; they differ in when negotiation
        # happens, and getting the pair wrong is the usual cause of a hang.
        start_tls=settings.SMTP_STARTTLS,
        use_tls=settings.SMTP_USE_TLS,
        timeout=10.0,
    )


async def send_email(to: str, subject: str, body: str) -> bool:
    """Deliver a message. Returns whether it was handed off successfully.

    Never raises: callers run inside request handlers whose response must not
    depend on the mail provider being up.
    """
    if not settings.SMTP_HOST:
        # Body included deliberately — the link is the whole point of the log
        # line in dev, and this backend is never selected once SMTP_HOST is set.
        logger.info("email.logged to=%s subject=%s\n%s", to, subject, body)
        return True

    try:
        await _send_smtp(_build(to, subject, body))
    except Exception as exc:
        # Address deliberately omitted: this lands in CloudWatch, and the point
        # of the flow is not to disclose which addresses have accounts.
        logger.warning("email.send_failed subject=%s error=%r", subject, exc)
        return False
    logger.info("email.sent subject=%s", subject)
    return True


def _link(path: str, token: str) -> str:
    """Absolute URL into the SPA for an emailed action.

    FRONTEND_ORIGIN may carry a comma-separated allowlist for CORS; the first
    entry is the canonical site (in the deployed stack it is the only entry) and
    is what a link in an email has to point at.
    """
    origin = next(
        (o.strip() for o in settings.FRONTEND_ORIGIN.split(",") if o.strip()),
        "http://localhost:5173",
    )
    return f"{origin.rstrip('/')}{path}?token={token}"


async def send_password_reset(to: str, name: str, token: str) -> bool:
    link = _link("/reset-password", token)
    return await send_email(
        to,
        "Reset your Aether password",
        f"Hi {name},\n\n"
        f"Use the link below to choose a new password. It expires in one hour "
        f"and can only be used once.\n\n{link}\n\n"
        f"If you didn't ask for this, you can ignore this email — your password "
        f"has not changed.\n",
    )


async def send_email_verification(to: str, name: str, token: str) -> bool:
    link = _link("/verify-email", token)
    return await send_email(
        to,
        "Confirm your Aether email address",
        f"Hi {name},\n\n"
        f"Confirm this address with the link below. It expires in 24 hours.\n\n"
        f"{link}\n\n"
        f"If you didn't create an Aether account, you can ignore this email.\n",
    )
