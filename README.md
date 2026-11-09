# Conduit

A self-hosted, OpenAI-compatible **LLM gateway**: one endpoint in front of Groq, Gemini and OpenRouter, with auth, rate limiting, caching, failover, cost tracking and monitoring.

> 🚧 Under active development. See [SPEC.md](SPEC.md) for the full design and [PROGRESS.md](PROGRESS.md) for what is built so far.

## Run it

Requires Docker.

```bash
docker compose up -d --build
curl localhost:8000/readyz
# {"status":"ready","checks":{"postgres":"ok","redis":"ok"}}
```

## Develop

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make install    # .venv + git hooks
make check      # lint + type check + all tests (what CI runs)
make help       # all commands
```

## Stack

Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · SQLAlchemy 2 (async) · Alembic · Docker Compose · GitHub Actions
