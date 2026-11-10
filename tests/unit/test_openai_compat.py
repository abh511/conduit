"""OpenAICompatibleProvider: payload forwarding and error mapping.

respx intercepts httpx at the transport level, so no real network is used.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.groq import GroqProvider
from app.providers.openrouter import OpenRouterProvider
from app.schemas import ChatRequest

BASE = "https://provider.test/v1"
URL = f"{BASE}/chat/completions"
OK: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "upstream-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def provider(http: httpx.AsyncClient) -> GroqProvider:
    return GroqProvider(api_key="sk-test", base_url=BASE, client=http)


def request(**extra: Any) -> ChatRequest:
    return ChatRequest.model_validate(
        {"model": "groq/upstream-model", "messages": [{"role": "user", "content": "hi"}], **extra}
    )


@respx.mock
async def test_forwards_only_what_the_client_sent(provider: GroqProvider) -> None:
    route = respx.post(URL).respond(json=OK)

    resp = await provider.chat(
        request(temperature=0, tools=[{"type": "function"}], stream=False), "upstream-model", 5
    )

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "model": "upstream-model",  # provider prefix stripped
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.0,
        "tools": [{"type": "function"}],  # unknown field passed through
    }  # no "stream", no max_tokens default
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"
    assert resp.choices[0].message.content == "ok"
    assert resp.usage is not None and resp.usage.total_tokens == 2


@respx.mock
async def test_openrouter_sends_its_title_header(http: httpx.AsyncClient) -> None:
    route = respx.post(URL).respond(json=OK)
    provider = OpenRouterProvider(api_key="sk-or", base_url=BASE + "/", client=http)

    await provider.chat(request(), "meta-llama/llama-3.3-70b-instruct", 5)

    assert provider.name == "openrouter"
    assert route.calls.last.request.headers["x-title"] == "Conduit"


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (400, ProviderBadRequest, False),
        (404, ProviderBadRequest, False),
        (422, ProviderBadRequest, False),
        (401, ProviderAuthError, False),
        (403, ProviderAuthError, False),
        (408, ProviderTimeout, True),
        (429, ProviderRateLimited, True),
        (500, ProviderUnavailable, True),
        (503, ProviderUnavailable, True),
    ],
)
@respx.mock
async def test_status_code_mapping(
    provider: GroqProvider, status: int, expected: type[ProviderError], retryable: bool
) -> None:
    respx.post(URL).respond(status, json={"error": {"message": "upstream says no"}})

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat(request(), "m", 5)

    assert type(exc_info.value) is expected
    assert exc_info.value.retryable is retryable
    assert exc_info.value.provider == "groq"
    assert exc_info.value.detail is not None and "upstream says no" in exc_info.value.detail


@respx.mock
async def test_bad_request_passes_on_the_provider_message(provider: GroqProvider) -> None:
    respx.post(URL).respond(400, json={"error": {"message": "max_tokens is too large"}})

    with pytest.raises(ProviderBadRequest) as exc_info:
        await provider.chat(request(), "m", 5)

    assert exc_info.value.message == "max_tokens is too large"


@respx.mock
async def test_bad_request_with_a_plain_string_error(provider: GroqProvider) -> None:
    respx.post(URL).respond(400, json={"error": "unknown parameter"})

    with pytest.raises(ProviderBadRequest) as exc_info:
        await provider.chat(request(), "m", 5)

    assert exc_info.value.message == "unknown parameter"


@respx.mock
async def test_bad_request_without_a_parseable_body(provider: GroqProvider) -> None:
    respx.post(URL).respond(400, text="<html>nope</html>")

    with pytest.raises(ProviderBadRequest) as exc_info:
        await provider.chat(request(), "m", 5)

    assert exc_info.value.message == "groq rejected the request (HTTP 400)"


@pytest.mark.parametrize(
    ("header", "expected"),
    [("12", 12.0), ("1.5", 1.5), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("-3", None)],
)
@respx.mock
async def test_retry_after_parsing(
    provider: GroqProvider, header: str, expected: float | None
) -> None:
    respx.post(URL).respond(429, headers={"retry-after": header})

    with pytest.raises(ProviderRateLimited) as exc_info:
        await provider.chat(request(), "m", 5)

    assert exc_info.value.retry_after_s == expected


@respx.mock
async def test_rate_limit_without_retry_after(provider: GroqProvider) -> None:
    respx.post(URL).respond(429)

    with pytest.raises(ProviderRateLimited) as exc_info:
        await provider.chat(request(), "m", 5)

    assert exc_info.value.retry_after_s is None


@respx.mock
async def test_timeout_maps_to_provider_timeout(provider: GroqProvider) -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    with pytest.raises(ProviderTimeout):
        await provider.chat(request(), "m", 5)


@respx.mock
async def test_connection_failure_maps_to_unavailable(provider: GroqProvider) -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(ProviderUnavailable):
        await provider.chat(request(), "m", 5)


@pytest.mark.parametrize("body", ["not json", '{"id": "x"}', "[]"])
@respx.mock
async def test_malformed_success_body_maps_to_unavailable(
    provider: GroqProvider, body: str
) -> None:
    respx.post(URL).respond(200, text=body)

    with pytest.raises(ProviderUnavailable, match="malformed"):
        await provider.chat(request(), "m", 5)
