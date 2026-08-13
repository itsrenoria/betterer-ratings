from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from betterer_ratings import api_server
from betterer_ratings.config.schema import AppConfig
from betterer_ratings.observability import log_buffer
from betterer_ratings.observability.logging_setup import configure_logging


def _record(
    *,
    level: int = logging.INFO,
    msg: str = "[Submitter] Queue status: ratings(pending=1).",
    **extra: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="betterer-ratings",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_buffer_preserves_structured_fields_from_json_formatter():
    handler = log_buffer.LogBufferHandler()
    handler.emit(_record(ratings_pending=1, event="queue.status"))

    entries = handler.snapshot()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "INFO"
    assert entry["logger"] == "betterer-ratings"
    assert entry["component"] == "submitter"
    assert entry["event"] == "queue.status"
    assert entry["ratings_pending"] == 1
    assert "timestamp" in entry


def test_buffer_is_bounded_and_evicts_oldest():
    handler = log_buffer.LogBufferHandler(maxlen=3)
    for i in range(5):
        handler.emit(_record(msg=f"entry {i}"))

    entries = handler.snapshot()
    assert len(entries) == 3
    assert [e["message"] for e in entries] == ["entry 2", "entry 3", "entry 4"]


def test_buffer_level_filter_uses_minimum_severity():
    handler = log_buffer.LogBufferHandler()
    handler.emit(_record(level=logging.DEBUG, msg="debug line"))
    handler.emit(_record(level=logging.WARNING, msg="warning line"))
    handler.emit(_record(level=logging.ERROR, msg="error line"))

    warning_and_up = [e["message"] for e in handler.snapshot(level="WARNING")]
    assert warning_and_up == ["warning line", "error line"]

    all_entries = [e["message"] for e in handler.snapshot(level="ALL")]
    assert all_entries == ["debug line", "warning line", "error line"]


def test_buffer_level_filter_ignores_unrecognized_level():
    handler = log_buffer.LogBufferHandler()
    handler.emit(_record(level=logging.DEBUG, msg="debug line"))
    handler.emit(_record(level=logging.ERROR, msg="error line"))

    entries = handler.snapshot(level="NOT_A_REAL_LEVEL")

    assert [e["message"] for e in entries] == ["debug line", "error line"]


def test_buffer_clear_empties_entries():
    handler = log_buffer.LogBufferHandler()
    handler.emit(_record())
    assert len(handler.snapshot()) == 1

    handler.clear()
    assert handler.snapshot() == []


def test_configure_logging_remains_single_stdout_handler(base_valid_config):
    config = AppConfig.from_mapping(base_valid_config)
    configure_logging(config)

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)
    assert "FileHandler" not in type(root.handlers[0]).__name__


def test_attach_once_adds_buffer_handler_after_configure_logging(base_valid_config):
    config = AppConfig.from_mapping(base_valid_config)
    configure_logging(config)

    handler = log_buffer.attach_once()
    root = logging.getLogger()

    assert len(root.handlers) == 2
    assert handler in root.handlers
    assert log_buffer.attach_once() is handler

    root.removeHandler(handler)
    log_buffer._buffer_handler = None


def test_attach_once_captures_real_log_calls_end_to_end(base_valid_config):
    config = AppConfig.from_mapping(base_valid_config)
    configure_logging(config)

    handler = log_buffer.attach_once()
    logger = logging.getLogger("betterer-ratings.integration-test")
    logger.warning("[Submitter] Integration warning fired.")
    logger.error("[Submitter] Integration error fired.")

    messages = [entry["message"] for entry in handler.snapshot()]
    assert "[Submitter] Integration warning fired." in messages
    assert "[Submitter] Integration error fired." in messages

    root = logging.getLogger()
    root.removeHandler(handler)
    log_buffer._buffer_handler = None


def test_handle_logs_returns_snapshot_filtered_by_query_level():
    calls: list[str | None] = []

    class FakeBuffer:
        def snapshot(self, level=None):
            calls.append(level)
            return [{"level": "ERROR", "logger": "x", "message": "boom", "timestamp": 123.0}]

    request = SimpleNamespace(app={"log_buffer": FakeBuffer()}, query={"level": "ERROR"})
    response = asyncio.run(api_server.handle_logs(request))
    payload = json.loads(response.text)

    assert payload["logs"] == [
        {"level": "ERROR", "logger": "x", "message": "boom", "timestamp": 123.0}
    ]
    assert calls == ["ERROR"]


def test_handle_logs_omits_level_when_not_requested():
    calls: list[str | None] = []

    class FakeBuffer:
        def snapshot(self, level=None):
            calls.append(level)
            return []

    request = SimpleNamespace(app={"log_buffer": FakeBuffer()}, query={})
    asyncio.run(api_server.handle_logs(request))

    assert calls == [None]
