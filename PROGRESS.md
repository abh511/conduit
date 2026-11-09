# Progress

## Phase 1: Skeleton ✅ (2026-11-09)

**Done-when check:** `docker compose up` brings up postgres → redis → migrate → api, and `/readyz` returns 200. Locally, lint, type check and 8 tests pass (100% coverage); CI is green once the repo is pushed to GitHub.

### What was built

| Piece | Files |
|---|---|
| Typed config from env vars | `app/config.py` |
| App factory + lifespan (DB pool, Redis pool) | `app/main.py` |
| Async SQLAlchemy engine, declarative base | `app/db/session.py`, `app/db/base.py` |
| Redis client | `app/redis_client.py` |
| `/healthz` (liveness), `/readyz` (readiness) | `app/api/health.py` |
| Async Alembic, URL read from Settings | `alembic/env.py`, `alembic.ini` |
| Docker image (multi-stage, non-root, healthcheck) | `Dockerfile`, `.dockerignore` |
| Compose stack: postgres, redis, migrate, api | `docker-compose.yml` |
| Lint/format/type-check config, pytest config | `pyproject.toml` |
| Pre-commit hooks | `.pre-commit-config.yaml` |
| CI: lint+mypy, tests (with testcontainers), docker build | `.github/workflows/ci.yml` |
| Unit tests (no Docker), integration tests (real PG + Redis) | `tests/` |

### Decisions and why

1. **Liveness vs readiness are separate.** `/healthz` checks nothing external; `/readyz` checks Postgres and Redis in parallel with a 2 s timeout each. If liveness depended on the DB, a DB outage would make the orchestrator restart every API container in a loop. Readiness only takes the instance out of rotation. *Verified:* stopping Redis gives `/readyz` 503 `{"redis":"error"}` while `/healthz` stays 200, and it recovers on its own when Redis returns.
2. **`/readyz` hides error details.** The failure reason is logged, not returned: exception text can contain hostnames or DSNs, and the endpoint has no auth.
3. **App factory (`create_app(settings)`).** Tests build an app pointed at throwaway containers without touching global state. Uvicorn runs it with `--factory`.
4. **Pools are created in the lifespan and connect lazily.** One DB pool and one Redis pool per process, closed on shutdown. The app can start while a dependency is still booting; readiness reports the truth. `pool_pre_ping` stops a Postgres restart from surfacing as errors on the next few requests.
5. **Migrations run as a separate one-shot `migrate` container.** The api starts only after it exits 0, so the app never runs against an old schema, and with several api replicas only one process migrates, so they don't race.
6. **One source of truth for config.** Alembic reads `CONDUIT_DATABASE_URL` through the same `Settings` class as the app; `alembic.ini` contains no credentials. The `asyncpg` driver is enforced by a validator, so a sync URL fails at startup, not at the first query.
7. **uv + `uv.lock`.** Docker and CI install exactly the locked versions (`--frozen`). The Dockerfile installs dependencies before copying code, so code-only changes rebuild in seconds.
8. **Image hardening.** Multi-stage build (no build tools in runtime), non-root `conduit` user, Postgres/Redis bound to `127.0.0.1` only. Image is about 214 MB.
9. **Pre-commit runs tools via `uv run`.** Commits use the same ruff/mypy versions as CI, so there's no "passes locally, fails in CI" from version drift.
10. **Real dependencies in integration tests (testcontainers), not mocks.** Unit tests (`make test-unit`) still run with no Docker.

Deliberately **not** in Phase 1: no tables yet (the first migration arrives with tenants/keys in Phase 3); stdlib logging, to be replaced by structlog + request IDs in Phase 2; image push to GHCR comes in Phase 6.

### How to run

```bash
# Full stack in Docker
docker compose up -d --build          # or: make up
curl localhost:8000/healthz           # {"status":"ok"}
curl localhost:8000/readyz            # {"status":"ready","checks":{"postgres":"ok","redis":"ok"}}
docker compose down                   # or: make down (keeps data volumes)

# Local development (API on host, deps in Docker)
make install                          # uv sync + pre-commit hooks
cp .env.example .env
docker compose up -d postgres redis
make dev                              # uvicorn with --reload on :8000

# Quality gates
make check                            # ruff + mypy + all tests with coverage
make test-unit                        # fast, no Docker
```

### Questions to be ready for (Phase 1)

- Liveness vs readiness: what does an orchestrator do when each fails?
- Why create the DB engine in the lifespan rather than at import time?
- Why does `/readyz` run its checks with `asyncio.gather` and a timeout?
- Why is migrating in a separate container safer than migrating in the API's startup?
- What does a lockfile guarantee, and why order the Dockerfile layers as deps-then-code?
