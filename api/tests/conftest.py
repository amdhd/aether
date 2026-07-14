import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.core.rate_limit import reset_rate_limits
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys, get_db, get_session_factory
from app.main import app

# Default to an in-memory SQLite DB (fast, keyless — mirrors local dev/CI). Set
# TEST_DATABASE_URL to a Postgres DSN to run the same suite against real
# Postgres + pgvector (see the CI "postgres" matrix leg), which is the only way
# to exercise the pgvector semantic-search and Postgres-only SQL paths.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite://")
IS_SQLITE = TEST_DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    # StaticPool keeps the single in-memory DB alive across sessions.
    engine = create_async_engine(
        TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
else:
    # pytest-asyncio runs each test in its own event loop; asyncpg connections
    # are bound to the loop that opened them, so a pooled connection reused in a
    # later test raises InterfaceError. NullPool opens a fresh connection per
    # use, keeping every connection on its own test's loop.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

enable_sqlite_foreign_keys(engine)
TestingSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db
# The streaming chat endpoint owns its own session via the factory; point it at
# the test engine so streamed writes land in the test DB, not the real one.
app.dependency_overrides[get_session_factory] = lambda: TestingSessionLocal


@pytest_asyncio.fixture(autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        if not IS_SQLITE:
            # The notes.embedding column is a native pgvector type on Postgres,
            # so the extension must exist before create_all emits the DDL.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    reset_rate_limits()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # pytest-asyncio gives each test its own event loop; asyncpg connections are
    # loop-bound, so dispose the engine between tests to guarantee the next test
    # opens its connections on its own loop (NullPool alone doesn't prevent a
    # cached connection from straddling loops).
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "name": "Test User", "password": "supersecret123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "supersecret123"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
