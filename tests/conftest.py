from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


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
