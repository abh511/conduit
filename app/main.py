"""App factory.

`create_app()` builds a fresh FastAPI instance. Tests call it with their own
Settings (pointing at testcontainers); production runs it through
`uvicorn app.main:create_app --factory`.

Shared resources (DB pool, Redis pool) are created in the lifespan, once per
process, and stored on `app.state`. They are closed on shutdown so in-flight
connections are released cleanly.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.redis_client import create_redis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = create_redis(settings)
        app.state.settings = settings
        app.state.db_engine = engine
        app.state.db_sessionmaker = create_session_factory(engine)
        app.state.redis = redis
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Conduit", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    return app
