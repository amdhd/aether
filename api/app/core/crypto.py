"""Encryption at rest for third-party credentials (Google OAuth tokens).

ENCRYPTION_KEY holds one Fernet key, or several separated by commas to rotate
without downtime: the first encrypts every new value, and every listed key can
still decrypt. To rotate, put a new key first, keep the old one after it, run
`python scripts/rotate_encryption_key.py` (from api/) to re-encrypt stored rows under the
new key, then drop the old one.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet

from app.core.config import settings


def parse_keys(raw: str) -> list[str]:
    return [key.strip() for key in (raw or "").split(",") if key.strip()]


@lru_cache
def _fernet() -> MultiFernet:
    keys = parse_keys(settings.ENCRYPTION_KEY)
    if not keys:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return MultiFernet([Fernet(key.encode()) for key in keys])


def encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def reencrypt_token(value: str) -> str:
    """Re-encrypt a stored value under the primary (first) key."""
    return _fernet().rotate(value.encode()).decode()
