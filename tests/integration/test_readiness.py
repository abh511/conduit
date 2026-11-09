import pytest
from alembic import command
from alembic.config import Config

from app.config import Settings
from app.main import create_app
from tests.conftest import running_app

pytestmark = pytest.mark.integration


async def test_readyz_is_ready_with_real_dependencies(settings: Settings) -> None:
    async with running_app(create_app(settings)) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"postgres": "ok", "redis": "ok"}}


def test_alembic_upgrade_and_downgrade_run_cleanly(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every migration must apply on an empty DB and roll back cleanly.

    Sync test on purpose: alembic's env.py calls asyncio.run(), which can't run
    inside an already-running event loop.
    """
    monkeypatch.setenv("CONDUIT_DATABASE_URL", settings.database_url)
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()
