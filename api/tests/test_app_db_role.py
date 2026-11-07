"""The API's database role can read and write rows, and nothing more."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.db.app_role import ensure_app_role, validate_app_role
from tests import conftest

PASSWORD = "A1b2C3d4E5f6G7h8I9j0K1l2"


@pytest.mark.parametrize("user,password", [
    ("aether_app", "short"),
    ("aether_app", "has'quote" + "x" * 30),
    ("Aether", PASSWORD),
    ("app; DROP TABLE users", PASSWORD),
    ("", PASSWORD),
])
def test_unsafe_role_names_and_passwords_are_refused(user, password):
    with pytest.raises(ValueError):
        validate_app_role(user, password)


def test_a_valid_role_is_accepted():
    validate_app_role("aether_app", PASSWORD)


@pytest.mark.skipif(conftest.IS_SQLITE, reason="roles exist only in Postgres")
async def test_the_app_role_has_dml_but_no_ddl():
    role = f"app_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(conftest.TEST_DATABASE_URL, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS app_role_probe (id serial PRIMARY KEY, v text)"))
        await conn.run_sync(lambda sync: ensure_app_role(sync, role, PASSWORD))
        # Idempotent: a second run (every deploy) must not fail.
        await conn.run_sync(lambda sync: ensure_app_role(sync, role, PASSWORD))

    as_app = create_async_engine(
        make_url(conftest.TEST_DATABASE_URL).set(username=role, password=PASSWORD), poolclass=NullPool
    )
    try:
        async with as_app.begin() as conn:
            await conn.execute(text("INSERT INTO app_role_probe (v) VALUES ('ok')"))
            assert (await conn.execute(text("SELECT count(*) FROM app_role_probe"))).scalar() >= 1
            await conn.execute(text("UPDATE app_role_probe SET v = 'changed'"))
            await conn.execute(text("DELETE FROM app_role_probe"))

        for ddl in [
            "CREATE TABLE app_role_forbidden (id int)",
            "DROP TABLE app_role_probe",
            "ALTER TABLE app_role_probe ADD COLUMN x int",
            "TRUNCATE app_role_probe",
            f"CREATE ROLE {role}_child",
        ]:
            with pytest.raises(Exception):
                async with as_app.begin() as conn:
                    await conn.execute(text(ddl))
    finally:
        await as_app.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS app_role_probe"))
            await conn.execute(text(f"DROP OWNED BY {role}"))
            await conn.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()
