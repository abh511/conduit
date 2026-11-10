"""One error hierarchy, one JSON error shape.

Every error the API returns looks like:

    {"error": {"type": "...", "message": "...", "request_id": "..."}}

Anything raised as a `ConduitError` carries its own HTTP status and type, so
endpoints just `raise` and the handlers below do the formatting.
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)


class ConduitError(Exception):
    status_code: int = 500
    error_type: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidRequest(ConduitError):
    status_code = 400
    error_type = "invalid_request_error"


class ModelNotFound(ConduitError):
    status_code = 404
    error_type = "model_not_found"


# ---------------------------------------------------------------- providers
# Every adapter translates its provider's failures into exactly these classes.
# `retryable` is what the Phase 4 retry loop will branch on: a retry can help
# with a timeout or a 5xx, but sending the same bad request again cannot.


class ProviderError(ConduitError):
    status_code = 502
    error_type = "provider_error"
    retryable: bool = False

    def __init__(self, message: str, *, provider: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        # Raw upstream text. Logged, never returned to the client.
        self.detail = detail


class ProviderTimeout(ProviderError):
    status_code = 504
    error_type = "provider_timeout"
    retryable = True


class ProviderRateLimited(ProviderError):
    """The *provider* rate limited the gateway.

    Returned as 503, not 429: 429 is reserved for the client exceeding its own
    Conduit quota (Phase 3). Here the client did nothing wrong.
    """

    status_code = 503
    error_type = "provider_rate_limited"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        detail: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, detail=detail)
        self.retry_after_s = retry_after_s


class ProviderUnavailable(ProviderError):
    """Provider 5xx, connection failure or an unparseable response."""

    status_code = 502
    error_type = "provider_unavailable"
    retryable = True


class ProviderAuthError(ProviderError):
    """The provider rejected the gateway's own API key: a server-side
    misconfiguration, so the client gets a 502 rather than a 401."""

    status_code = 502
    error_type = "provider_auth_error"


class ProviderBadRequest(ProviderError):
    """Provider said the request itself is invalid (4xx). Never retried."""

    status_code = 400
    error_type = "provider_bad_request"


# ---------------------------------------------------------------- formatting

_STATUS_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limit_exceeded",
}


def error_body(error_type: str, message: str, request_id: str | None) -> dict[str, Any]:
    return {"error": {"type": error_type, "message": message, "request_id": request_id}}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _handle_conduit_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConduitError)  # noqa: S101 - narrowing for mypy
    if isinstance(exc, ProviderError):
        log.warning(
            "provider_error",
            error_type=exc.error_type,
            provider=exc.provider,
            detail=exc.detail,
        )
    headers = {}
    if isinstance(exc, ProviderRateLimited) and exc.retry_after_s is not None:
        headers["Retry-After"] = str(max(1, round(exc.retry_after_s)))
    return JSONResponse(
        error_body(exc.error_type, exc.message, _request_id(request)),
        status_code=exc.status_code,
        headers=headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Report the first problem as "body.messages: Field required". The rejected
    # input is deliberately not echoed back: it may contain user data.
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return JSONResponse(
        error_body("invalid_request_error", f"{location}: {first['msg']}", _request_id(request)),
        status_code=400,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    error_type = _STATUS_TYPES.get(exc.status_code, "http_error")
    return JSONResponse(
        error_body(error_type, str(exc.detail), _request_id(request)),
        status_code=exc.status_code,
        headers=dict(exc.headers) if exc.headers else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ConduitError, _handle_conduit_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    # Unhandled exceptions (500) are caught in RequestContextMiddleware, which
    # sits outside these handlers and can still attach the request ID.
