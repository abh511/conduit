"""/v1/chat/completions through the full app (middleware, handlers, registry)."""

import uuid
from typing import Any

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from app.main import create_app
from app.providers.mock import MockProvider
from tests.conftest import make_settings, running_app

BODY: dict[str, Any] = {
    "model": "mock/test-model",
    "messages": [{"role": "user", "content": "hello there gateway"}],
}


def assert_error(resp: httpx.Response, status: int, error_type: str) -> dict[str, Any]:
    assert resp.status_code == status
    error: dict[str, Any] = resp.json()["error"]
    assert set(error) == {"type", "message", "request_id"}
    assert error["type"] == error_type
    assert error["request_id"] == resp.headers["x-request-id"]
    return error


async def test_chat_returns_openai_shaped_response() -> None:
    async with running_app(create_app(make_settings(mock_completion_tokens=5))) as client:
        resp = await client.post("/v1/chat/completions", json=BODY)

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "mock/test-model"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert resp.headers["x-conduit-provider"] == "mock"


async def test_every_response_gets_a_unique_request_id() -> None:
    async with running_app(create_app(make_settings())) as client:
        first = await client.post("/v1/chat/completions", json=BODY)
        # A client-supplied ID is ignored: the gateway always generates its own.
        second = await client.get("/healthz", headers={"x-request-id": "spoofed"})

    ids = [first.headers["x-request-id"], second.headers["x-request-id"]]
    assert ids[0] != ids[1]
    for request_id in ids:
        uuid.UUID(request_id)  # raises if not a valid UUID


async def test_access_log_carries_request_fields() -> None:
    async with running_app(create_app(make_settings())) as client:
        with capture_logs() as logs:
            resp = await client.post("/v1/chat/completions", json=BODY)

    access = next(line for line in logs if line["event"] == "request")
    assert access["method"] == "POST"
    assert access["path"] == "/v1/chat/completions"
    assert access["status"] == 200
    assert access["duration_ms"] >= 0

    completion = next(line for line in logs if line["event"] == "chat_completion")
    assert completion["provider"] == "mock"
    assert completion["provider_model"] == "test-model"
    # Prompt text must never reach the logs.
    assert "hello there gateway" not in str(logs)
    assert resp.status_code == 200


async def test_stream_true_is_rejected_for_now() -> None:
    async with running_app(create_app(make_settings())) as client:
        resp = await client.post("/v1/chat/completions", json={**BODY, "stream": True})

    assert_error(resp, 400, "invalid_request_error")


@pytest.mark.parametrize(
    "model",
    ["no-slash", "mock/", "nosuchprovider/some-model", "groq/llama-3.3-70b-versatile"],
)
async def test_unresolvable_model_is_404(model: str) -> None:
    # groq/... is 404 here because no Groq API key is configured.
    async with running_app(create_app(make_settings())) as client:
        resp = await client.post("/v1/chat/completions", json={**BODY, "model": model})

    error = assert_error(resp, 404, "model_not_found")
    assert "mock" in error["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"model": "mock/m"},  # no messages
        {"model": "mock/m", "messages": []},
        {"messages": [{"role": "user", "content": "hi"}]},  # no model
        {**BODY, "temperature": 9},
        {**BODY, "max_tokens": 0},
    ],
)
async def test_invalid_body_is_400_in_the_unified_shape(body: dict[str, Any]) -> None:
    async with running_app(create_app(make_settings())) as client:
        resp = await client.post("/v1/chat/completions", json=body)

    assert_error(resp, 400, "invalid_request_error")


async def test_validation_error_does_not_echo_the_input() -> None:
    body = {"model": "mock/m", "messages": [{"content": "my secret prompt"}]}
    async with running_app(create_app(make_settings())) as client:
        resp = await client.post("/v1/chat/completions", json=body)

    error = assert_error(resp, 400, "invalid_request_error")
    assert error["message"] == "body.messages.0.role: Field required"
    assert "my secret prompt" not in resp.text


async def test_unknown_route_and_wrong_method_use_the_unified_shape() -> None:
    async with running_app(create_app(make_settings())) as client:
        missing = await client.get("/v1/nope")
        wrong_method = await client.get("/v1/chat/completions")

    assert_error(missing, 404, "not_found")
    assert_error(wrong_method, 405, "method_not_allowed")


async def test_provider_failure_is_502() -> None:
    async with running_app(create_app(make_settings(mock_failure_rate=1.0))) as client:
        resp = await client.post("/v1/chat/completions", json=BODY)

    assert_error(resp, 502, "provider_unavailable")


async def test_provider_timeout_is_504() -> None:
    settings = make_settings(mock_latency_ms=500, provider_timeout_s=0.01)
    async with running_app(create_app(settings)) as client:
        resp = await client.post("/v1/chat/completions", json=BODY)

    assert_error(resp, 504, "provider_timeout")


async def test_unhandled_exception_is_500_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(MockProvider, "chat", boom)
    async with running_app(create_app(make_settings())) as client:
        with capture_logs() as logs:
            resp = await client.post("/v1/chat/completions", json=BODY)

    error = assert_error(resp, 500, "internal_error")
    assert "secret" not in error["message"]
    assert any(line["event"] == "unhandled_error" for line in logs)
    assert any(line["event"] == "request" and line["status"] == 500 for line in logs)


# ------------------------------------------------- real provider, mocked HTTP

GROQ_URL = "https://groq.test/openai/v1"
UPSTREAM_OK = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "llama-3.3-70b-versatile",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi!"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    "x_groq": {"id": "req_123"},
}


@respx.mock
async def test_groq_request_is_proxied_end_to_end() -> None:
    route = respx.post(f"{GROQ_URL}/chat/completions").respond(json=UPSTREAM_OK)
    settings = make_settings(groq_api_key="gsk-test", groq_base_url=GROQ_URL)

    async with running_app(create_app(settings)) as client:
        resp = await client.post(
            "/v1/chat/completions", json={**BODY, "model": "groq/llama-3.3-70b-versatile"}
        )

    assert resp.status_code == 200
    assert resp.headers["x-conduit-provider"] == "groq"
    data = resp.json()
    assert data["model"] == "groq/llama-3.3-70b-versatile"  # the name the client used
    assert data["choices"][0]["message"]["content"] == "hi!"
    assert data["x_groq"] == {"id": "req_123"}  # unknown provider fields pass through
    assert route.calls.last.request.headers["authorization"] == "Bearer gsk-test"


@respx.mock
async def test_provider_rate_limit_is_503_with_retry_after() -> None:
    respx.post(f"{GROQ_URL}/chat/completions").respond(
        429, headers={"retry-after": "7"}, json={"error": {"message": "slow down, key gsk-..."}}
    )
    settings = make_settings(groq_api_key="gsk-test", groq_base_url=GROQ_URL)

    async with running_app(create_app(settings)) as client:
        resp = await client.post("/v1/chat/completions", json={**BODY, "model": "groq/m"})

    error = assert_error(resp, 503, "provider_rate_limited")
    assert resp.headers["retry-after"] == "7"
    assert "slow down" not in error["message"]  # upstream text stays in the logs


async def test_providers_are_registered_only_when_their_key_is_set() -> None:
    settings = make_settings(openrouter_api_key="sk-or-test")
    async with running_app(create_app(settings)) as client:
        resp = await client.post("/v1/chat/completions", json={**BODY, "model": "nope/x"})

    assert "available providers: mock, openrouter" in resp.json()["error"]["message"]
