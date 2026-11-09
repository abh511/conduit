"""Health endpoints with no Postgres/Redis available.

The app is pointed at ports where nothing listens. Engine and Redis client
connect lazily, so the app still starts, and this is exactly the situation the
liveness/readiness split exists for.
"""

import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import running_app


@pytest.fixture
def unreachable_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://u:p@127.0.0.1:1/none",
        redis_url="redis://127.0.0.1:1/0",
        readiness_timeout_s=1.0,
    )


async def test_healthz_is_ok_even_when_dependencies_are_down(
    unreachable_settings: Settings,
) -> None:
    async with running_app(create_app(unreachable_settings)) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_returns_503_when_dependencies_are_down(
    unreachable_settings: Settings,
) -> None:
    async with running_app(create_app(unreachable_settings)) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "not_ready",
        "checks": {"postgres": "error", "redis": "error"},
    }
