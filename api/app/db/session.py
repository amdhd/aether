from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, future=True)
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
