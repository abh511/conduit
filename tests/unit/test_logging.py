import json
import logging

import pytest
import structlog

from app.config import Settings
from app.observability.logging import configure_logging


def test_json_logs_include_context_and_stdlib_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    structlog.contextvars.bind_contextvars(request_id="req-1")
    try:
        structlog.get_logger("conduit.test").info("hello", provider="mock")
        logging.getLogger("some.library").warning("from stdlib")
        try:
            raise ValueError("boom")
        except ValueError:
            structlog.get_logger("conduit.test").exception("failed")
    finally:
        structlog.contextvars.clear_contextvars()

    ours, stdlib, failed = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert ours["event"] == "hello"
    assert ours["provider"] == "mock"
    assert ours["request_id"] == "req-1"
    assert ours["level"] == "info"
    assert ours["logger"] == "conduit.test"
    assert ours["timestamp"].endswith("Z")
    assert stdlib["event"] == "from stdlib"
    assert stdlib["request_id"] == "req-1"
    assert "ValueError: boom" in failed["exception"]


def test_console_format_is_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "console")
    structlog.get_logger("conduit.test").info("hello")

    out = capsys.readouterr().out
    assert "hello" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_api_keys_are_not_printed_and_empty_means_unset() -> None:
    settings = Settings(_env_file=None, groq_api_key="gsk-very-secret", openrouter_api_key="")

    assert "gsk-very-secret" not in repr(settings)
    assert settings.openrouter_api_key is None
