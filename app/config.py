"""Application settings, loaded from environment variables (and `.env` in local dev).

Every setting has one source of truth here. Code never reads `os.environ` directly:
it receives a `Settings` object, which keeps configuration explicit and easy to
override in tests.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    # "json" for machines (production), "console" for humans (local dev).
    log_format: Literal["json", "console"] = "json"

    # Postgres. The asyncpg driver is required because the whole app is async.
    database_url: str = "postgresql+asyncpg://conduit:conduit@localhost:5432/conduit"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    redis_url: str = "redis://localhost:6379/0"

    # How long /readyz waits on each dependency before declaring it unhealthy.
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    # Providers. A provider is enabled only when its API key is set.
    # SecretStr keeps keys out of logs and reprs (they print as '**********').
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Upper bound on one provider call. Per-route timeouts arrive in Phase 4.
    provider_timeout_s: float = Field(default=30.0, gt=0)

    # Mock provider behaviour (tests and load tests).
    mock_latency_ms: int = Field(default=20, ge=0)
    mock_failure_rate: float = Field(default=0.0, ge=0, le=1)
    mock_completion_tokens: int = Field(default=32, ge=1)

    @field_validator("groq_api_key", "openrouter_api_key", mode="before")
    @classmethod
    def _empty_key_means_unset(cls, v: object) -> object:
        # docker compose passes unset variables as "", which must not count
        # as "a key is configured".
        return None if v == "" else v

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
