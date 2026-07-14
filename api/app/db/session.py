from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """SQLite ships with foreign-key enforcement OFF, so `ON DELETE CASCADE`
    (which the ORM leans on via `passive_deletes=True`) silently does nothing
    and children orphan. Turn it on per connection. No-op on Postgres, which
    enforces foreign keys natively."""
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = create_async_engine(settings.DATABASE_URL, future=True)
enable_sqlite_foreign_keys(engine)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency that returns the session *factory* (not a session) for callers
    that must own their own session lifecycle beyond the request — notably the
    streaming chat endpoint, whose generator runs after the request's `get_db`
    dependency has been torn down. Overridden in tests to bind the test engine."""
    return AsyncSessionLocal
