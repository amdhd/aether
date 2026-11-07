"""One account per address, whatever its capitalisation.

"Alice@X.com" and "alice@x.com" reach the same inbox, so they must name the
same account: for registration, sign-in, password reset, and at the database.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.user import User, normalize_email
from tests.conftest import TestingSessionLocal

API_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "supersecret123"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    async def capture(to: str, name: str, token: str) -> bool:
        captured.append(to)
        return True

    monkeypatch.setattr("app.api.routes.auth.email.send_password_reset", capture)
    monkeypatch.setattr("app.api.routes.auth.email.send_email_verification", capture)
    return captured


def test_normalize_email_trims_and_lowercases() -> None:
    assert normalize_email("  Alice.Smith@Example.COM ") == "alice.smith@example.com"


async def test_registration_stores_the_normalised_address(client: AsyncClient, sent: list[str]) -> None:
    resp = await client.post("/api/v1/auth/register",
                             json={"email": "  Alice@Example.COM ", "name": "A", "password": PASSWORD})
    assert resp.status_code < 300
    async with TestingSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email_is("alice@example.com")))
    assert user is not None and user.email == "alice@example.com"


async def test_a_differently_cased_duplicate_cannot_register(client: AsyncClient, sent: list[str]) -> None:
    await client.post("/api/v1/auth/register", json={"email": "bob@example.com", "name": "B", "password": PASSWORD})
    await client.post("/api/v1/auth/register", json={"email": "BOB@Example.com", "name": "Imposter", "password": "another-password"})
    async with TestingSessionLocal() as db:
        rows = (await db.execute(User.__table__.select().where(User.email_is("bob@example.com")))).all()
    assert len(rows) == 1 and rows[0].name == "B"


@pytest.mark.parametrize("typed", ["carol@example.com", "CAROL@EXAMPLE.COM", " Carol@Example.com "])
async def test_sign_in_ignores_case_and_whitespace(client: AsyncClient, sent: list[str], typed: str) -> None:
    await client.post("/api/v1/auth/register", json={"email": "Carol@Example.com", "name": "C", "password": PASSWORD})
    resp = await client.post("/api/v1/auth/login", data={"username": typed, "password": PASSWORD})
    assert resp.status_code == 200


async def test_a_legacy_mixed_case_row_is_still_found(client: AsyncClient, sent: list[str]) -> None:
    """Rows written before normalisation keep their casing; lookups must match them."""
    from app.core.security import hash_password

    async with TestingSessionLocal() as db:
        db.add(User(email="Legacy.User@Example.com", name="L", password_hash=hash_password(PASSWORD)))
        await db.commit()
    login = await client.post("/api/v1/auth/login", data={"username": "legacy.user@example.com", "password": PASSWORD})
    assert login.status_code == 200

    sent.clear()
    await client.post("/api/v1/auth/forgot-password", json={"email": "LEGACY.USER@example.com"})
    assert sent == ["Legacy.User@Example.com"]


async def test_the_database_rejects_a_case_only_duplicate() -> None:
    async with TestingSessionLocal() as db:
        db.add(User(email="dave@example.com", name="D", password_hash="x"))
        await db.commit()
        db.add(User(email="Dave@Example.com", name="D2", password_hash="x"))
        with pytest.raises(IntegrityError):
            await db.commit()


def _alembic(db: Path, target: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=API_ROOT,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": f"sqlite+aiosqlite:///{db}", "ENVIRONMENT": "development"},
        capture_output=True,
        text=True,
    )


def test_the_migration_stops_on_existing_case_duplicates(tmp_path: Path) -> None:
    db = tmp_path / "dupes.db"
    assert _alembic(db, "d2a6b8c0e4f1").returncode == 0
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO users (email, password_hash, name, token_version) VALUES (?, 'x', 'n', 0)",
            [("eve@example.com",), ("Eve@Example.com",)],
        )
    result = _alembic(db, "head")
    assert result.returncode != 0
    assert "more than one account" in result.stderr
    # The address itself is not echoed into deploy logs.
    assert "eve@example.com" not in result.stderr.lower()
