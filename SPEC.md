# Conduit: Production LLM Gateway

**Project spec, build plan and Claude Code brief** · Author: Abhishek Bhosale · Target: Backend SDE / AI Engineer fresher roles (India, 2027 batch) · Replaces: EnviroWatch AI on the resume

---

## 0. How to use this document with Claude Code

Put this file in the root of a new repo as `SPEC.md` and start Claude Code with:

> Read SPEC.md fully. We will build this project phase by phase, exactly in the order of Section 9. Do NOT start the next phase until I say so. For every phase: first explain the design and the trade-offs to me, then let me write the core logic myself where Section 12 marks it as "write yourself", review my code, and only then generate the supporting code (boilerplate, tests, configs). After each phase, update `PROGRESS.md` with what was built, decisions taken and how to run it.

**Important rule for you, not for Claude:** every interviewer will ask you to explain this project line by line. Code you cannot explain hurts you more than missing features. Section 12 lists the parts you must write and understand yourself.

---

## 1. Why this project

### What it is

Conduit is a self-hosted **API gateway that sits between applications and LLM providers** (Groq, Gemini, OpenRouter, etc.). Apps call one OpenAI-compatible endpoint; Conduit handles authentication, rate limiting, caching, routing, failover, cost tracking, PII redaction and monitoring.

Real products in this space: LiteLLM, Portkey, Cloudflare AI Gateway, Kong AI Gateway. Every company running LLMs in production needs this layer, which is why it is a hiring signal.

### Why it fits your journey

| Your goal | How Conduit serves it |
|---|---|
| Backend SDE roles | A pure backend/distributed-systems project: async APIs, Redis, Postgres, queues, rate limiting, caching, circuit breakers, streaming, observability, CI/CD, cloud. |
| AI Engineer / GenAI roles | LLM infrastructure (LLMOps): multi-provider routing, token accounting, semantic caching, guardrails. |
| System design interviews | Rate limiter, cache, circuit breaker and API gateway are among the most commonly asked system design topics. You will have *built* them. |
| Portfolio story | Route **AskDB** and **AwarePDF** through Conduit: "I built two AI apps and the production infrastructure they run on." |
| Gaps found in your resume | Adds cloud (AWS), Redis, CI/CD, auth, load testing and real performance numbers. |

### Why not something else

- A CRUD app (e-commerce, blog, todo) does not stand out anywhere.
- Another RAG/chatbot overlaps with AwarePDF.
- A pure ML project does not help SDE shortlisting.

---

## 2. Feature scope

### MVP (must have, all of these go on the resume)

1. **OpenAI-compatible endpoint**: `POST /v1/chat/completions` accepts the OpenAI request format so any OpenAI SDK client works by changing `base_url`.
2. **Multiple providers behind one interface**: Groq, Google Gemini, OpenRouter, plus a **Mock provider** (configurable latency, failure rate, token counts) used for tests and load tests so you never burn real API quota.
3. **API keys and tenants**: admin creates projects and API keys; keys are stored **hashed** (SHA-256 with a prefix shown to users, like `cnd_live_ab12…`); each key has quotas and allowed models.
4. **Rate limiting**: per-key **requests per minute (RPM)** and **tokens per minute (TPM)** using a **token-bucket algorithm in Redis implemented as an atomic Lua script**. Returns `429` with `Retry-After` and `X-RateLimit-*` headers.
5. **Routing and failover**: per-model route config (primary provider, fallbacks). Retries with **exponential backoff + jitter**, request timeout, and a **circuit breaker per provider** (closed → open → half-open) so a failing provider is skipped automatically.
6. **Exact-match response cache**: Redis cache keyed by a hash of the normalized request (model, messages, temperature, etc.), with TTL; only for `temperature == 0` or when the client sends `x-conduit-cache: force`. Response header `x-conduit-cache: HIT|MISS`.
7. **Streaming**: Server-Sent Events (SSE) pass-through for `stream: true`, with token counting at stream end.
8. **Usage and cost tracking**: every request records tenant, key, model, provider, prompt/completion tokens, latency, cache hit, status and computed cost (from a model pricing table). Writes are **asynchronous**: the request path pushes an event to a **Redis Stream**, and a separate **worker** consumes and batch-inserts into Postgres. The request path never waits on the usage DB write.
9. **Usage API**: `GET /v1/usage` (per key: totals by day/model), admin endpoints to manage tenants, keys, routes.
10. **Observability**: structured JSON logs with a request ID, **Prometheus metrics** (request count, latency histogram p50/p95/p99, cache hit ratio, provider errors, circuit-breaker state, rate-limit rejections), and a **Grafana dashboard** committed as JSON.
11. **Testing**: unit tests + integration tests against real Postgres and Redis using **testcontainers**; target 80%+ coverage on core modules.
12. **Load testing**: **k6** or **Locust** scripts against the Mock provider; record throughput and latency numbers for the README and resume.
13. **CI/CD**: GitHub Actions: `ruff` lint, `mypy` type check, tests, Docker image build and push to GHCR.
14. **Deployment**: Docker Compose stack (api, worker, postgres, redis, prometheus, grafana) deployed on **AWS EC2**, with HTTPS via Caddy, public demo URL.

### Stretch (only after MVP is fully done and deployed)

- **Semantic cache**: embed the prompt (sentence-transformers or a hosted embedding API), store in **pgvector**, return cached answer if cosine similarity is above a threshold; measure hit rate and false-hit rate.
- **PII redaction guardrail**: detect and mask emails, phone numbers, Aadhaar/PAN-like patterns before the prompt leaves the gateway (regex first; Microsoft Presidio optional).
- **Budget limits**: monthly spend cap per tenant; reject or downgrade the model when exceeded.
- **Cost-aware routing**: route to the cheapest healthy provider meeting a latency target.
- **OpenTelemetry tracing** with Jaeger.
- **Minimal dashboard UI** (React) for usage graphs.

### Explicitly out of scope

Billing/payments, user signup UI, Kubernetes, multi-region, fine-tuning. Do not add these.

---

## 3. Architecture

```
          Client apps (AskDB, AwarePDF, curl, OpenAI SDK)
                         │ HTTPS, Bearer API key
                         ▼
┌─────────────────────── Conduit API (FastAPI, async) ───────────────────────┐
│ Middleware: request ID → auth (key lookup, cached) → rate limiter (Redis)  │
│        → PII guard (stretch) → cache lookup (Redis / pgvector)             │
│                               │ miss                                       │
│                               ▼                                            │
│ Router: route config → circuit breaker check → provider adapter            │
│         retries + backoff + timeout → fallback provider                    │
│                               │                                            │
│ Response: cache write → emit usage event to Redis Stream → return / SSE    │
└──────────────┬──────────────────────────────┬──────────────────────────────┘
               │                              │
       ┌───────▼──────┐               ┌───────▼────────┐
       │    Redis     │               │  Usage worker  │  consumes stream,
       │ rate limits, │◄──────────────│    (async)     │  batch-inserts
       │ cache, CB    │    stream     └───────┬────────┘
       │ state,stream │                       ▼
       └──────────────┘               ┌────────────────┐
                                      │   PostgreSQL   │  tenants, keys, routes,
                                      │  (+ pgvector)  │  usage, pricing
                                      └────────────────┘
   Providers: Groq │ Gemini │ OpenRouter │ Mock
   Monitoring: /metrics → Prometheus → Grafana
```

### Key design decisions (be ready to defend each)

| Decision | Reason | Trade-off |
|---|---|---|
| Token bucket in Redis via Lua | Atomic check-and-decrement; works across multiple API instances | Redis becomes a dependency on the hot path; decide fail-open vs fail-closed (choose fail-open for availability and document it) |
| Async usage logging via Redis Streams | Keeps p99 latency low; DB slowness doesn't slow requests | Eventual consistency; usage may lag a few seconds; need consumer groups + ack for at-least-once delivery |
| Circuit breaker state in Redis | Shared across instances | Small extra Redis call per request |
| Hashed API keys | A DB leak doesn't leak usable keys | Need a fast lookup → hash the incoming key and look up by hash; cache key metadata in Redis for 60 s |
| Exact cache only at temperature 0 | Non-deterministic requests shouldn't return identical answers | Lower hit rate |
| Provider adapter interface | Adding a provider = one new class | Must normalize different error formats and token counting |

---

## 4. Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.12, fully type-hinted |
| API | FastAPI (async), Pydantic v2, Uvicorn |
| HTTP client | httpx (async), with timeouts |
| Database | PostgreSQL 16, SQLAlchemy 2.0 (async) + asyncpg, Alembic migrations; pgvector (stretch) |
| Cache / limits / queue | Redis 7 (Lua scripts, Streams, consumer groups) |
| Tokens | tiktoken for estimating prompt tokens before the call; provider-reported usage after |
| Observability | prometheus-client, structlog, Grafana |
| Testing | pytest, pytest-asyncio, testcontainers, respx (mock httpx), coverage |
| Load test | k6 (preferred) or Locust |
| Quality | ruff, mypy, pre-commit |
| Infra | Docker, Docker Compose, GitHub Actions, AWS EC2, Caddy (HTTPS) |

---

## 5. Data model (PostgreSQL)

```
tenants(id uuid pk, name text unique, monthly_budget_usd numeric null, created_at)
api_keys(id uuid pk, tenant_id fk, key_prefix text, key_hash text unique,
         name text, rpm_limit int, tpm_limit int, allowed_models text[] null,
         is_active bool, created_at, last_used_at)
model_routes(id uuid pk, public_model text unique,      -- e.g. "fast-chat"
             targets jsonb,  -- ordered list: [{provider, model, timeout_s, weight}]
             cache_ttl_s int, created_at)
model_pricing(provider text, model text, input_per_1m_usd numeric,
              output_per_1m_usd numeric, pk(provider, model))
usage_events(id bigserial pk, request_id uuid unique, tenant_id, api_key_id,
             public_model, provider, provider_model, prompt_tokens int,
             completion_tokens int, cost_usd numeric(12,6), latency_ms int,
             cache_status text, status_code int, error_type text null,
             created_at timestamptz)       -- index (api_key_id, created_at)
semantic_cache(id, embedding vector(384), request_hash, response jsonb, created_at)  -- stretch
```

`request_id unique` makes the worker's inserts **idempotent** (safe with at-least-once delivery).

---

## 6. API surface

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /v1/chat/completions` | API key | OpenAI-compatible chat, supports `stream` |
| `GET /v1/models` | API key | List public models this key may use |
| `GET /v1/usage?from=&to=&group_by=day\|model` | API key | Usage and cost for this key |
| `POST /admin/tenants`, `GET /admin/tenants` | Admin token | Manage tenants |
| `POST /admin/keys` (returns raw key once), `DELETE /admin/keys/{id}` | Admin token | Manage keys |
| `PUT /admin/routes/{public_model}` | Admin token | Configure routing/fallbacks |
| `GET /healthz`, `GET /readyz` | none | Liveness / readiness (checks DB + Redis) |
| `GET /metrics` | internal | Prometheus |

Errors use one JSON shape: `{"error": {"type": "...", "message": "...", "request_id": "..."}}` with correct status codes (400, 401, 403, 404, 429, 502, 503, 504).

---

## 7. Repository layout

```
conduit/
├── SPEC.md  PROGRESS.md  README.md  docker-compose.yml  Dockerfile  Makefile
├── .github/workflows/ci.yml
├── alembic/  alembic.ini
├── app/
│   ├── main.py              # app factory, middleware, routers
│   ├── config.py            # pydantic-settings
│   ├── api/                 # chat.py, models.py, usage.py, admin.py, health.py
│   ├── core/
│   │   ├── auth.py          # key hashing, lookup, caching
│   │   ├── rate_limiter.py  # token bucket + lua/token_bucket.lua
│   │   ├── circuit_breaker.py
│   │   ├── router.py        # route resolution, retries, fallback
│   │   ├── cache.py         # exact (and semantic) cache
│   │   ├── tokens.py        # tiktoken estimates
│   │   └── guardrails.py    # PII redaction (stretch)
│   ├── providers/           # base.py (interface), groq.py, gemini.py, openrouter.py, mock.py
│   ├── db/                  # models.py, session.py, repositories
│   ├── workers/usage_worker.py
│   └── observability/       # metrics.py, logging.py
├── tests/  unit/  integration/
├── loadtest/  k6_chat.js  RESULTS.md
└── ops/  grafana/dashboard.json  prometheus.yml  Caddyfile
```

---

## 8. Provider adapter contract

```python
class Provider(Protocol):
    name: str

    async def chat(self, req: ChatRequest, model: str, timeout_s: float) -> ChatResponse: ...
    async def stream(
        self, req: ChatRequest, model: str, timeout_s: float
    ) -> AsyncIterator[ChatChunk]: ...


# Every adapter maps provider errors to one internal hierarchy:
# ProviderTimeout, ProviderRateLimited, ProviderUnavailable (5xx), ProviderBadRequest (4xx, never retried)
```

Retry policy: retry only on timeout/429/5xx, max 2 retries, backoff `base * 2^attempt` + random jitter; never retry 4xx bad requests. After retries are exhausted on a target, record a circuit-breaker failure and move to the next fallback.

---

## 9. Build plan (about 6–7 weeks at 2–3 hrs/day, alongside DSA)

Each phase ends with: tests passing in CI, `PROGRESS.md` updated, a commit, and you able to explain it.

**Phase 1: Skeleton (Week 1)** Repo, Docker Compose (api, postgres, redis), config, Alembic, health endpoints, ruff/mypy/pre-commit, GitHub Actions running tests. *Done when:* `docker compose up` works and CI is green.

**Phase 2: Core proxy (Week 2)** Provider interface, Mock + Groq + one more provider, `/v1/chat/completions` non-streaming, unified errors, request IDs, structured logs. *Done when:* the official OpenAI Python SDK with `base_url` pointed at Conduit gets a response.

**Phase 3: Auth + rate limiting (Week 3)** Tenants, hashed API keys, admin endpoints, Redis-cached key lookup, token-bucket RPM and TPM via Lua, 429 + headers. *Done when:* a concurrency test (e.g. 200 parallel requests on a 60 RPM key) allows exactly the expected count.

**Phase 4: Reliability (Week 4)** Route config, retries with backoff + jitter, timeouts, circuit breaker with half-open probing, fallback chain. Use the Mock provider to inject failures. *Done when:* tests prove that with the primary failing 100%, requests succeed via fallback and the breaker opens, then recovers.

**Phase 5: Cache, streaming, usage pipeline (Week 5)** Exact cache, SSE streaming, Redis Stream + worker with consumer group, idempotent batch inserts, pricing table, `/v1/usage`. *Done when:* repeated request shows HIT; streaming works with the OpenAI SDK; usage rows appear within seconds; killing the worker then restarting loses no events.

**Phase 6: Observability, load test, deploy (Week 6)** Prometheus metrics, Grafana dashboard, k6 load tests (record results), deploy to AWS EC2 with Caddy HTTPS, CI builds and pushes image. *Done when:* a public URL works, the dashboard shows live traffic and `loadtest/RESULTS.md` has numbers.

**Phase 7: Integrate and polish (Week 7)** Point AskDB and AwarePDF LLM calls at Conduit. README with architecture diagram, design decisions, load-test results, a demo GIF and "how to run in 2 commands". **Then stretch features**, semantic cache first.

---

## 10. Numbers to measure (these become resume bullets)

Run on the deployed EC2 instance against the Mock provider (fixed 200 ms latency) so results reflect gateway overhead, not the LLM.

- Sustained throughput (requests/sec) at p95 latency under a target
- **Gateway overhead**: p50/p95/p99 added latency vs calling the mock directly
- Cache: latency on HIT vs MISS; hit rate on a replayed realistic workload
- Reliability: success rate with the primary provider failing 30% / 100% of the time (with vs without fallback)
- Rate limiter accuracy under concurrency (allowed vs expected)
- Test count and coverage %

Report honestly, including instance size (e.g. "t3.micro, 2 workers").

---

## 11. Resume bullets (fill in real numbers only after measuring)

**Conduit: Production LLM Gateway** · Python, FastAPI, PostgreSQL, Redis, Docker, AWS, Prometheus, GitHub Actions · github.com/abh511/conduit · live demo

- Built an OpenAI-compatible gateway that routes requests across Groq, Gemini and OpenRouter with retries, exponential backoff, per-provider circuit breakers and automatic fallback, keeping success rate at **X%** while the primary provider failed **30%** of requests.
- Implemented per-key RPM/TPM rate limiting with an atomic Redis Lua token bucket, hashed API keys and multi-tenant quotas; verified exact enforcement under **N** concurrent requests.
- Moved usage and cost logging off the request path with Redis Streams and an idempotent batch-insert worker, sustaining **X req/s** at **Y ms** p95 gateway overhead in k6 load tests on AWS EC2.
- Added a response cache (**X%** hit rate, **Y×** faster on hits), SSE streaming, Prometheus/Grafana dashboards and a GitHub Actions CI pipeline with **N** tests (**Z%** coverage); AskDB and AwarePDF now run their LLM calls through it.

Put Conduit **first** in the SDE resume version; AskDB first in the AI Engineer version.

---

## 12. Write these yourself (with Claude explaining, not writing)

Interviewers will ask about these. Write them by hand, then let Claude review:

1. Token bucket Lua script and the Python wrapper
2. Circuit breaker state machine
3. Retry/backoff/fallback loop in the router
4. Cache key normalization and hashing
5. Redis Stream consumer with ack and idempotent insert

Claude Code can generate: boilerplate, Pydantic schemas, Alembic migrations, provider HTTP plumbing, tests, Docker/CI/Grafana configs, README.

---

## 13. Interview questions you must be able to answer

- Why token bucket over fixed window or sliding window log? What happens at the window boundary?
- Why must the rate-limit check be atomic? What breaks without Lua?
- If Redis goes down, what does your gateway do? Why did you choose that?
- Explain closed/open/half-open. How do you choose thresholds?
- Why jitter in backoff? What is a retry storm?
- Which requests are safe to retry? Why never retry a 400?
- Why process usage asynchronously? What delivery guarantee do you have, and how do you avoid double counting?
- How would you scale to 10× traffic? Where is the bottleneck? (Run the load test and know the answer.)
- How do you count tokens for a streaming response?
- Why hash API keys? Why not encrypt them?
- When is caching an LLM response wrong?
- What does p99 mean and why does it matter more than average?

---

## 14. Definition of done

- [ ] Public HTTPS URL + demo key for reviewers (with low limits)
- [ ] README: problem, architecture diagram, design decisions table, load-test results, run in 2 commands
- [ ] CI green badge, coverage badge
- [ ] Grafana dashboard screenshot in README
- [ ] AskDB and AwarePDF use Conduit
- [ ] 2-minute demo video linked from README and resume
- [ ] You can whiteboard the architecture and answer Section 13 without notes

**Cost note:** use the Mock provider for all load testing and provider free tiers for demos (check each provider's current free-tier limits). An EC2 free-tier-eligible instance keeps hosting near zero; set an AWS billing alarm on day one.
