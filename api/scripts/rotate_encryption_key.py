"""Re-encrypt stored Google credentials under the primary ENCRYPTION_KEY.

Run after putting a new key first in ENCRYPTION_KEY (old key(s) after it):

    cd api && python scripts/rotate_encryption_key.py

Once it reports every row rotated, the old key can be removed. Prints counts
only — never token values.
"""

import asyncio

from sqlalchemy import select

from app.core.crypto import reencrypt_token
from app.db.session import AsyncSessionLocal
from app.models.google_credential import GoogleCredential


async def _rotate() -> int:
    async with AsyncSessionLocal() as db:
        credentials = list(await db.scalars(select(GoogleCredential)))
        for credential in credentials:
            credential.access_token_encrypted = reencrypt_token(credential.access_token_encrypted)
            credential.refresh_token_encrypted = reencrypt_token(credential.refresh_token_encrypted)
        await db.commit()
    print(f"Re-encrypted {len(credentials)} Google credential(s) under the primary key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_rotate()))
