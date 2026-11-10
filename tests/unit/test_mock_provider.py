import random
import time

import pytest

from app.errors import ProviderTimeout, ProviderUnavailable
from app.providers.mock import MockProvider
from app.schemas import ChatRequest

REQ = ChatRequest.model_validate(
    {
        "model": "mock/m",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": [{"type": "text", "text": "parts are not counted"}]},
            {"role": "user", "content": "one two three"},
        ],
    }
)


async def test_token_counts_are_deterministic() -> None:
    resp = await MockProvider(completion_tokens=7).chat(REQ, "m", 1)

    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 5
    assert resp.usage.completion_tokens == 7
    assert resp.usage.total_tokens == 12
    content = resp.choices[0].message.content
    assert isinstance(content, str) and len(content.split()) == 7
    assert resp.id.startswith("chatcmpl-")


async def test_latency_is_applied() -> None:
    start = time.perf_counter()
    await MockProvider(latency_s=0.05).chat(REQ, "m", 1)

    assert time.perf_counter() - start >= 0.05


async def test_times_out_when_latency_exceeds_timeout() -> None:
    start = time.perf_counter()
    with pytest.raises(ProviderTimeout):
        await MockProvider(latency_s=10).chat(REQ, "m", 0.02)

    assert time.perf_counter() - start < 1


async def test_failure_rate_extremes() -> None:
    with pytest.raises(ProviderUnavailable):
        await MockProvider(failure_rate=1.0).chat(REQ, "m", 1)
    await MockProvider(failure_rate=0.0).chat(REQ, "m", 1)


async def test_failure_rate_is_roughly_honoured() -> None:
    provider = MockProvider(failure_rate=0.3, rng=random.Random(42))
    failures = 0
    for _ in range(1000):
        try:
            await provider.chat(REQ, "m", 1)
        except ProviderUnavailable:
            failures += 1

    assert 250 <= failures <= 350
