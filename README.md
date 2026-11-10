# Conduit

A self-hosted, OpenAI-compatible **LLM gateway**: one endpoint in front of Groq, Gemini and OpenRouter, with auth, rate limiting, caching, failover, cost tracking and monitoring.

> 🚧 Under active development. See [SPEC.md](SPEC.md) for the full design and [PROGRESS.md](PROGRESS.md) for what is built so far.

## Run it

Requires Docker.

```bash
docker compose up -d --build
curl -s localhost:8000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock/demo","messages":[{"role":"user","content":"hello"}]}'
```

Any OpenAI SDK works by changing `base_url`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused-until-phase-3")
r = client.chat.completions.create(
    model="mock/demo", messages=[{"role": "user", "content": "hello"}]
)
```

Models are addressed as `provider/model`. `mock/...` always works; `groq/...` and `openrouter/...` work once their API key is set in `.env` (see [.env.example](.env.example)).

## Develop

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make install    # .venv + git hooks
make check      # lint + type check + all tests (what CI runs)
make help       # all commands
```

## Stack

Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · SQLAlchemy 2 (async) · Alembic · httpx · structlog · Docker Compose · GitHub Actions
