"""Adapter for any provider that exposes the OpenAI chat-completions API.

Groq and OpenRouter both do, so they share this class and differ only in
base URL, API key and headers. Its real job is the error mapping: turning
whatever httpx or the provider throws into Conduit's own error hierarchy.
"""

from typing import Any

import httpx
from pydantic import ValidationError

from app.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.schemas import ChatRequest, ChatResponse

_MAX_DETAIL_CHARS = 500


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
        self._client = client

    async def chat(self, req: ChatRequest, model: str, timeout_s: float) -> ChatResponse:
        # exclude_unset: forward exactly what the client sent, without adding
        # our defaults (e.g. don't send temperature if the client didn't).
        payload = req.model_dump(exclude_unset=True)
        payload["model"] = model
        payload.pop("stream", None)

        try:
            resp = await self._client.post(
                self._url, json=payload, headers=self._headers, timeout=timeout_s
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{self.name} did not respond within {timeout_s:g}s", provider=self.name
            ) from exc
        except httpx.TransportError as exc:
            # DNS failure, connection refused, connection reset...
            raise ProviderUnavailable(
                f"could not reach {self.name}", provider=self.name, detail=repr(exc)
            ) from exc

        if resp.status_code >= 400:
            raise self._error_from_response(resp)

        try:
            return ChatResponse.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderUnavailable(
                f"{self.name} returned a malformed response",
                provider=self.name,
                detail=resp.text[:_MAX_DETAIL_CHARS],
            ) from exc

    def _error_from_response(self, resp: httpx.Response) -> ProviderError:
        status = resp.status_code
        detail = resp.text[:_MAX_DETAIL_CHARS]

        if status == 429:
            return ProviderRateLimited(
                f"{self.name} is rate limiting the gateway",
                provider=self.name,
                detail=detail,
                retry_after_s=_parse_retry_after(resp.headers.get("retry-after")),
            )
        if status in (401, 403):
            return ProviderAuthError(
                f"gateway could not authenticate with {self.name}",
                provider=self.name,
                detail=detail,
            )
        if status == 408:
            return ProviderTimeout(f"{self.name} timed out", provider=self.name, detail=detail)
        if status >= 500:
            return ProviderUnavailable(
                f"{self.name} returned HTTP {status}", provider=self.name, detail=detail
            )
        # Remaining 4xx: the request itself is wrong (bad parameter, unknown
        # model, context too long). The provider's message is about the
        # client's own input, so it is safe and useful to pass on.
        return ProviderBadRequest(
            _upstream_message(resp) or f"{self.name} rejected the request (HTTP {status})",
            provider=self.name,
            detail=detail,
        )


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After in seconds. The HTTP-date form is ignored."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _upstream_message(resp: httpx.Response) -> str | None:
    """Extract `error.message` from an OpenAI-style error body, if present."""
    try:
        body: Any = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return None
