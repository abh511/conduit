"""Real Postgres and Redis in throwaway Docker containers, one pair per test session.

Integration tests talk to the same database engines as production instead of
mocks, so things like SQL dialect quirks and Redis command semantics are
actually exercised.
"""

from collections.abc import Iterator

import pytest
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.config import Settings


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as rd:
        host = rd.get_container_host_ip()
        port = rd.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
def settings(postgres_url: str, redis_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=postgres_url,
        redis_url=redis_url,
    )
