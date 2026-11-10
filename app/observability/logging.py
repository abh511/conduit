"""Structured logging with structlog.

Every log line is one JSON object, so a log system can filter on fields
(`request_id`, `status`, `provider`) instead of grepping free text. Fields bound
with `structlog.contextvars` (the request ID) are added to every line logged
while handling that request, including lines from libraries.
"""

import logging
import sys

import structlog
from structlog.typing import Processor


def configure_logging(level: str, log_format: str) -> None:
    # Processors that run for every line, ours and third-party alike.
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    renderers: list[Processor]
    if log_format == "json":
        renderers = [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        renderers = [structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    # Route the standard library's logging (uvicorn, SQLAlchemy, ...) through
    # the same pipeline, so the whole process emits one consistent format.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderers],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; drop them so its lines go through ours.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # uvicorn's per-request access line is switched off: RequestContextMiddleware
    # writes a richer one (request ID, duration) for every request.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
    # httpx logs every outgoing request at INFO; we log provider calls ourselves.
    logging.getLogger("httpx").setLevel(logging.WARNING)
