"""Application settings, loaded from environment variables (and `.env` in local dev).

Every setting has one source of truth here. Code never reads `os.environ` directly:
it receives a `Settings` object, which keeps configuration explicit and easy to
override in tests.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONDUIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "conduit"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Postgres. The asyncpg driver is required because the whole app is async.
    database_url: str = "postgresql+asyncpg://conduit:conduit@localhost:5432/conduit"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    redis_url: str = "redis://localhost:6379/0"

    # How long /readyz waits on each dependency before declaring it unhealthy.
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the async driver: postgresql+asyncpg://...")
        return v


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, built once on first use."""
    return Settings()
