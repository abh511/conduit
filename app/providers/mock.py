"""A fake provider with configurable latency, failure rate and token counts.

Used by tests and load tests, so they are fast, deterministic and never burn
real API quota. Because latency is a known constant, load-test numbers measure
the gateway's own overhead rather than an LLM's.
"""

import asyncio
import random
import time
import uuid

from app.errors import ProviderTimeout, ProviderUnavailable
from app.schemas import ChatMessage, ChatRequest, ChatResponse, Choice, Usage

_WORDS = ("conduit", "routes", "every", "request", "through", "one", "gateway")


class MockProvider:
    name = "mock"

    def __init__(
        self,
        *,
        latency_s: float = 0.0,
        failure_rate: float = 0.0,
        completion_tokens: int = 32,
        rng: random.Random | None = None,
    ) -> None:
        self._latency_s = latency_s
        self._failure_rate = failure_rate
        self._completion_tokens = completion_tokens
        # Injectable so tests can pass a seeded generator.
        self._rng = rng or random.Random()  # noqa: S311 - not used for security

    async def chat(self, req: ChatRequest, model: str, timeout_s: float) -> ChatResponse:
        if self._latency_s > timeout_s:
            await asyncio.sleep(timeout_s)
            raise ProviderTimeout(f"mock did not respond within {timeout_s:g}s", provider=self.name)
        await asyncio.sleep(self._latency_s)

        if self._rng.random() < self._failure_rate:
            raise ProviderUnavailable("mock returned HTTP 500", provider=self.name)

        # One "token" per word: crude, but stable and cheap. Real token
        # counting (tiktoken) arrives with TPM rate limiting.
        prompt_tokens = sum(
            len(m.content.split()) for m in req.messages if isinstance(m.content, str)
        )
        content = " ".join(_WORDS[i % len(_WORDS)] for i in range(self._completion_tokens))
        return ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=self._completion_tokens,
                total_tokens=prompt_tokens + self._completion_tokens,
            ),
        )
