import pytest
from pydantic import ValidationError

from app.config import Settings


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONDUIT_REDIS_URL", "redis://example:6380/2")
    monkeypatch.setenv("CONDUIT_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.redis_url == "redis://example:6380/2"
    assert settings.log_level == "DEBUG"


def test_rejects_sync_postgres_driver() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        Settings(_env_file=None, database_url="postgresql://u:p@h/db")


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="staging")


def test_readiness_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, readiness_timeout_s=0)
