"""Liveness and readiness probes.

/healthz (liveness): "is this process alive and serving HTTP?" It checks nothing
external. If liveness depended on Postgres, a database outage would make the
orchestrator restart every API container in a loop, which fixes nothing and
turns a partial outage into a full one.

/readyz (readiness): "should this instance receive traffic right now?" It checks
Postgres and Redis. A failing readiness check takes the instance out of the load
balancer until its dependencies come back, without restarting it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


async def _check_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis(redis: Redis) -> None:
    await redis.ping()


async def _run_check(
    name: str, check: Callable[[], Awaitable[None]], timeout_s: float
) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_s):
            await check()
    except Exception:
        # The reason goes to the logs, not the response: error text can contain
        # hostnames or connection strings, and this endpoint is unauthenticated.
        logger.exception("readiness check failed: %s", name)
        return "error"
    return "ok"


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(request: Request, response: Response) -> ReadinessResponse:
    state = request.app.state
    timeout_s: float = state.settings.readiness_timeout_s

    # Run both checks concurrently: total wait is max(), not sum(), of the two.
    pg, rd = await asyncio.gather(
        _run_check("postgres", lambda: _check_postgres(state.db_engine), timeout_s),
        _run_check("redis", lambda: _check_redis(state.redis), timeout_s),
    )
    checks: dict[str, CheckStatus] = {"postgres": pg, "redis": rd}

    if all(v == "ok" for v in checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="not_ready", checks=checks)
