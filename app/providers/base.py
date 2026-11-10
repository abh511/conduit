"""The contract every provider adapter implements.

The rest of the gateway only ever talks to this interface, so adding a provider
is one new class and nothing else changes.

It is a `Protocol` (structural typing): a class is a Provider if it has these
members, with no inheritance needed. mypy checks it wherever a Provider is
expected.

`stream()` from the spec is added in Phase 5 together with SSE.
"""

from typing import Protocol

from app.schemas import ChatRequest, ChatResponse


class Provider(Protocol):
    name: str

    async def chat(self, req: ChatRequest, model: str, timeout_s: float) -> ChatResponse:
        """Send `req` to the provider's `model` and return a normalized response.

        Raises only `app.errors.ProviderError` subclasses, whatever went wrong.
        """
        ...
