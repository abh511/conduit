"""Async SQLAlchemy engine and session factory.

One engine (and so one connection pool) per process, created in the app lifespan.
Creating an engine does not open a connection: connections are opened lazily on
first use, so the app can start even while Postgres is still booting.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Checks a pooled connection is still alive before handing it out, so a
        # Postgres restart doesn't surface as errors on the next few requests.
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit without a
    # surprise lazy-load, which would fail in async code.
    return async_sessionmaker(engine, expire_on_commit=False)
