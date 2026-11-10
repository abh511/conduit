from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings


@asynccontextmanager
async def running_app(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Run the app's lifespan (startup/shutdown) and yield an httpx client that
    calls it in-process, with no real network socket.

    httpx's ASGITransport alone does not trigger lifespan events, so without
    LifespanManager the DB engine and Redis client would never be created.
    """
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def make_settings(**overrides: Any) -> Settings:
    """Settings for unit tests: Postgres/Redis point at ports where nothing
    listens (both connect lazily, so the app still starts), and the mock
    provider answers instantly."""
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": "postgresql+asyncpg://u:p@127.0.0.1:1/none",
        "redis_url": "redis://127.0.0.1:1/0",
        "readiness_timeout_s": 1.0,
        "mock_latency_ms": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
