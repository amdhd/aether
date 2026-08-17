"""Admin utility: set a user's password directly (local/dev recovery).

Passwords are stored as one-way bcrypt hashes, so a forgotten password can't be
recovered — only replaced. This prompts for the new password with getpass (never
echoed, never passed on the command line, so it stays out of shell history),
writes a fresh hash for the given user via the app's own hashing, and signs out
every existing session so the reset actually locks out whoever held the old
credential.

    cd api
    ./.venv/bin/python scripts/reset_password.py <email>

Uses DATABASE_URL from the environment/.env (defaults to the local SQLite DB), so
it targets whatever database the app itself uses.
"""

import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services import refresh_tokens

MIN_PASSWORD_LENGTH = 8


async def _reset(email: str) -> int:
    password = getpass.getpass(f"New password for {email}: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return 1
    if password != getpass.getpass("Confirm new password: "):
        print("Passwords do not match.")
        return 1

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No user found with email {email!r}.")
            return 1
        user.password_hash = hash_password(password)
        # A reset is the recovery path for an account that may be compromised, so
        # replacing the hash is only half the job: it has to end the sessions the
        # old password bought. revoke_all_for_user revokes every outstanding
        # refresh token and bumps token_version, which invalidates unexpired
        # access tokens too, and commits that together with the new hash. Without
        # it a stolen refresh token survives the reset and rotates itself
        # indefinitely — the one action the user believes locks an intruder out
        # would leave them logged in.
        await refresh_tokens.revoke_all_for_user(db, user)

    print(f"Password updated for {email}. Other sessions have been signed out; you can sign in now.")
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/reset_password.py <email>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_reset(sys.argv[1])))


if __name__ == "__main__":
    main()
