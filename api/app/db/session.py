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


# SQLite (local dev/tests) doesn't support the pool tuning below, so only apply
# it to the real async Postgres engine used in deployed environments.
_engine_kwargs: dict[str, object] = {"future": True}
if settings.DATABASE_URL.startswith("postgresql+asyncpg://"):
    _engine_kwargs.update(
        # pool_pre_ping validates a connection before use so requests survive an
        # RDS failover / idle-timeout drop instead of erroring on a dead socket.
        pool_pre_ping=True,
        # Recycle connections well under RDS's idle cutoff to avoid stale sockets.
        pool_recycle=1800,
        # Sized so N Fargate tasks stay within RDS max_connections, roughly:
        # (pool_size + max_overflow) * task_count <= max_connections.
        pool_size=5,
        max_overflow=5,
    )

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
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
