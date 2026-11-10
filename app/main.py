"""App factory.

`create_app()` builds a fresh FastAPI instance. Tests call it with their own
Settings (pointing at testcontainers); production runs it through
`uvicorn app.main:create_app --factory`.

Shared resources (DB pool, Redis pool, outbound HTTP client) are created in the
lifespan, once per process, and stored on `app.state`. They are closed on
shutdown so in-flight connections are released cleanly.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api import chat, health
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.errors import register_error_handlers
from app.middleware import RequestContextMiddleware
from app.observability.logging import configure_logging
from app.providers.registry import build_registry
from app.redis_client import create_redis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = create_redis(settings)
        # One client for all provider calls: it keeps TCP/TLS connections open
        # and reuses them, instead of a new handshake on every request.
        http_client = httpx.AsyncClient(timeout=settings.provider_timeout_s)
        app.state.settings = settings
        app.state.db_engine = engine
        app.state.db_sessionmaker = create_session_factory(engine)
        app.state.redis = redis
        app.state.http_client = http_client
        app.state.providers = build_registry(settings, http_client)
        try:
            yield
        finally:
            await http_client.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Conduit", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(chat.router)
    return app
