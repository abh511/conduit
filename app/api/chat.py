"""POST /v1/chat/completions: the OpenAI-compatible endpoint.

Phase 2 flow: validate → resolve provider → call it → return its response.
Auth, rate limiting, caching, retries and fallback slot in around the provider
call in later phases.
"""

import time

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.errors import InvalidRequest
from app.providers.registry import ProviderRegistry
from app.schemas import ChatRequest, ChatResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(body: ChatRequest, request: Request) -> JSONResponse:
    if body.stream:
        raise InvalidRequest("stream=true is not supported yet")

    registry: ProviderRegistry = request.app.state.providers
    provider, upstream_model = registry.resolve(body.model)

    start = time.perf_counter()
    result = await provider.chat(
        body, upstream_model, timeout_s=request.app.state.settings.provider_timeout_s
    )
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    # Token counts and timing only. Prompts and completions are never logged.
    usage = result.usage
    log.info(
        "chat_completion",
        model=body.model,
        provider=provider.name,
        provider_model=upstream_model,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        provider_latency_ms=latency_ms,
    )

    # Echo back the model name the client asked for, not the provider's
    # internal one. Extra fields the provider sent are kept as they are.
    payload = result.model_copy(update={"model": body.model}).model_dump()
    return JSONResponse(payload, headers={"x-conduit-provider": provider.name})
