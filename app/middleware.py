"""Request context: request ID, access log, and the last-resort 500 handler.

Written as a plain ASGI middleware rather than Starlette's BaseHTTPMiddleware:
it runs in the same task as the endpoint (so context variables carry over),
adds no buffering, and will not get in the way of streaming in Phase 5.
"""

import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_body

log = structlog.get_logger("conduit.access")

# Probed every few seconds by Docker / load balancers: logged at DEBUG only.
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always generated here, never taken from the client: later phases use
        # it as the idempotency key for usage events, so it must be unique.
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # A bug, not an expected error. Log the traceback; tell the client
            # nothing except the request ID they can quote when reporting it.
            log.exception("unhandled_error")
            if response_started:
                raise
            response = JSONResponse(
                error_body("internal_error", "internal server error", request_id),
                status_code=500,
            )
            await response(scope, receive, send_with_request_id)
        finally:
            emit = log.debug if scope["path"] in _QUIET_PATHS else log.info
            emit(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
