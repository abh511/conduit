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

---

## Phase 2: Core proxy ✅ (2026-11-10)

**Done-when check:** the official OpenAI Python SDK, with `base_url` pointed at Conduit, gets a chat completion. Verified two ways: an automated test (`tests/unit/test_openai_sdk.py`) and by hand against the running Docker stack. 63 tests pass, 99% coverage, ruff and mypy clean.

Not verified: a call to the real Groq or OpenRouter API. No API keys were available, so those adapters are tested against mocked HTTP responses only. Add a key to `.env` and run the curl below to check a real one.

### What was built

| Piece | Files |
|---|---|
| OpenAI-compatible request/response models | `app/schemas.py` |
| Error hierarchy + the single JSON error shape | `app/errors.py` |
| Provider interface (`Protocol`) | `app/providers/base.py` |
| Shared adapter for OpenAI-style APIs, with error mapping | `app/providers/openai_compat.py` |
| Groq and OpenRouter adapters | `app/providers/groq.py`, `app/providers/openrouter.py` |
| Mock provider (latency, failure rate, token counts) | `app/providers/mock.py` |
| Provider registry and `provider/model` resolution | `app/providers/registry.py` |
| `POST /v1/chat/completions` (non-streaming) | `app/api/chat.py` |
| Request ID, access log, last-resort 500 handler | `app/middleware.py` |
| structlog JSON logging (also captures uvicorn/stdlib) | `app/observability/logging.py` |

### Request flow

```
client → RequestContextMiddleware (new request ID, start timer)
       → FastAPI validates the body into ChatRequest          (invalid → 400)
       → registry.resolve("groq/llama-3.3-70b-versatile")     (unknown → 404)
       → provider.chat(...) → httpx POST to the provider      (failure → ProviderError)
       → JSON response + x-request-id + x-conduit-provider
       → access log line (method, path, status, duration_ms, request_id)
```

### Decisions and why

1. **Models are addressed as `provider/model`** (`groq/llama-3.3-70b-versatile`, `mock/anything`), split on the first slash. It is the simplest scheme that works and is temporary: Phase 4 replaces it with route config (public names like `fast-chat`, with fallbacks).
2. **One adapter class for Groq and OpenRouter.** Both speak the OpenAI wire format, so they share `OpenAICompatibleProvider` and differ only in URL, key and headers. A provider with its own format (native Gemini) would be a separate class implementing the same `Provider` protocol.
3. **Provider errors are normalized into one hierarchy**, each with an HTTP status and a `retryable` flag that Phase 4's retry loop will use:

   | Upstream | Internal error | Client sees | Retryable |
   |---|---|---|---|
   | timeout, HTTP 408 | `ProviderTimeout` | 504 | yes |
   | HTTP 429 | `ProviderRateLimited` | 503 (+ `Retry-After`) | yes |
   | HTTP 5xx, connection failure, malformed body | `ProviderUnavailable` | 502 | yes |
   | HTTP 401/403 | `ProviderAuthError` | 502 | no |
   | other 4xx | `ProviderBadRequest` | 400 | no |

4. **A provider's 429 becomes 503, not 429.** 429 is reserved for a client exceeding its own Conduit quota (Phase 3). When the provider throttles the gateway, the client did nothing wrong.
5. **A provider's 401/403 becomes 502** (`ProviderAuthError`, an addition to the spec's four classes). It means the gateway's own key is bad. Passing it on as 400/401 would wrongly blame the client.
6. **Upstream error text goes to the logs, not the client**, except for `ProviderBadRequest`, where the message is about the client's own input ("max_tokens is too large") and is useful to them.
7. **The request ID is always generated by the gateway**; a client-supplied `x-request-id` is ignored. It becomes the idempotency key for usage events in Phase 5, so it must be unique.
8. **The middleware is plain ASGI**, not `BaseHTTPMiddleware`: it runs in the same task as the endpoint (so the request ID bound to the logging context is visible everywhere) and will not interfere with streaming in Phase 5. It also catches unhandled exceptions, so even a 500 has the unified shape and a request ID.
9. **Unknown request fields are forwarded** (`extra="allow"`; `tools`, `response_format`, ...). Only fields the client actually sent go upstream (`exclude_unset`), so the gateway never injects its own defaults.
10. **Logs never contain prompts or completions**: only model, provider, token counts and timings. A test asserts this.
11. **One shared `httpx.AsyncClient`** for all provider calls, created in the lifespan, so TCP/TLS connections are reused.
12. **Validation errors are 400, not FastAPI's default 422**, in the unified shape, and do not echo the rejected input.

Deliberately **not** in Phase 2: no auth (any `Authorization` header is accepted and ignored until Phase 3), `stream: true` returns 400 (Phase 5), no retries or fallback (Phase 4), Gemini (the spec asks for "Groq + one more"; OpenRouter was chosen because it shares the adapter).

### How to run

```bash
make up
curl -s localhost:8000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock/demo","messages":[{"role":"user","content":"hello"}]}'
```

With the OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused-until-phase-3")
r = client.chat.completions.create(
    model="mock/demo", messages=[{"role": "user", "content": "hello"}]
)
print(r.choices[0].message.content)
```

Real providers: put `CONDUIT_GROQ_API_KEY=...` (or `CONDUIT_OPENROUTER_API_KEY=...`) in `.env`, run `make up` again, and use `"model": "groq/llama-3.3-70b-versatile"`.

Mock behaviour: `CONDUIT_MOCK_LATENCY_MS`, `CONDUIT_MOCK_FAILURE_RATE` (0 to 1).

### Questions to be ready for (Phase 2)

- What does "OpenAI-compatible" mean in practice, and why is it valuable?
- Why a `Protocol` for providers rather than an abstract base class?
- Why is a provider's 429 a 503 to the client? Why is its 401 a 502?
- Which provider errors are safe to retry, and why never a 400?
- Why generate the request ID server-side instead of trusting the client's?
- Why plain ASGI middleware over `BaseHTTPMiddleware`?
- Why one shared HTTP client instead of one per request?
- Why must prompts stay out of the logs?
