"""Phase 2's done-when: the official OpenAI Python SDK works against Conduit.

The SDK is given an httpx client that calls the app in-process; everything
else (request building, response parsing, error classes) is the real SDK.
"""

from collections.abc import AsyncIterator

import httpx2
import openai
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from tests.conftest import make_settings


@pytest.fixture
async def sdk() -> AsyncIterator[openai.AsyncOpenAI]:
    async with LifespanManager(create_app(make_settings())) as manager:
        # httpx2 is the HTTP library the SDK itself is built on.
        http_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=manager.app))
        async with openai.AsyncOpenAI(
            base_url="http://conduit.test/v1",
            api_key="not-checked-until-phase-3",
            http_client=http_client,
            max_retries=0,
        ) as client:
            yield client


async def test_sdk_chat_completion(sdk: openai.AsyncOpenAI) -> None:
    completion = await sdk.chat.completions.create(
        model="mock/test-model",
        messages=[
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say hello"},
        ],
        temperature=0,
    )

    assert completion.object == "chat.completion"
    assert completion.model == "mock/test-model"
    assert completion.choices[0].message.role == "assistant"
    assert completion.choices[0].message.content
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None
    assert completion.usage.total_tokens == (
        completion.usage.prompt_tokens + completion.usage.completion_tokens
    )
    assert completion._request_id  # SDK picked up our x-request-id header


async def test_sdk_maps_our_errors_to_its_exception_classes(sdk: openai.AsyncOpenAI) -> None:
    with pytest.raises(openai.NotFoundError) as exc_info:
        await sdk.chat.completions.create(
            model="nosuchprovider/x", messages=[{"role": "user", "content": "hi"}]
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.type == "model_not_found"
    assert "nosuchprovider" in exc_info.value.message
    assert exc_info.value.request_id
