import asyncio
import logging

import httpx
import pytest

from betterer_ratings.infra.http.client import HTTPClient
from betterer_ratings.services.submit.runner import QueueAlertMonitor


class _FakeHTTPXClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.closed = 0
        self.requests = 0

    async def request(self, *_args, **_kwargs):
        self.requests += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self):
        self.closed += 1


def _response():
    return httpx.Response(
        200,
        json={"ok": True},
        request=httpx.Request("GET", "https://example.test/data"),
    )


class _FakeGate:
    name = "provider"

    def __init__(self, *, acquired=True, remaining=0):
        self.acquired = acquired
        self.remaining = remaining
        self.paused: list[tuple[int, str]] = []
        self.acquire_budgets: list[int | None] = []

    async def acquire(self, max_pause_wait_seconds=None):
        self.acquire_budgets.append(max_pause_wait_seconds)
        return self.acquired

    def observe_headers(self, _headers, _status):
        pass

    def pause_for(self, seconds, reason):
        self.paused.append((seconds, reason))

    def pause_remaining(self):
        return self.remaining


def _rate_limited(retry_after: int):
    return httpx.Response(
        429,
        headers={"Retry-After": str(retry_after)},
        json={"error": "rate limited"},
        request=httpx.Request("GET", "https://example.test/data"),
    )


def test_long_retry_after_is_persisted_and_returned_without_inline_sleep(monkeypatch, caplog):
    transport = _FakeHTTPXClient([_rate_limited(3600), _response()])
    gate = _FakeGate()
    sleeps: list[float] = []
    client = HTTPClient(timeout_seconds=5, max_retries=3, client_factory=lambda: transport)

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("betterer_ratings.infra.http.client.asyncio.sleep", record_sleep)

    with caplog.at_level(logging.WARNING, logger="betterer-ratings"):
        result = asyncio.run(
            client.request_json(
                method="GET",
                url="https://example.test/data",
                gate=gate,
                max_pause_wait_seconds=20,
            )
        )

    assert result.status == 429
    assert result.headers["retry-after"] == "3600"
    assert transport.requests == 1
    assert sleeps == []
    assert gate.paused == [(3600, "429 Too Many Requests")]
    deferred = next(
        record for record in caplog.records if getattr(record, "event", "") == "http.rate_limit_deferred"
    )
    assert deferred.retry_after_seconds == 3600


def test_existing_long_pause_returns_remaining_retry_after_without_request():
    transport = _FakeHTTPXClient([_response()])
    gate = _FakeGate(acquired=False, remaining=1800)
    client = HTTPClient(timeout_seconds=5, max_retries=3, client_factory=lambda: transport)

    result = asyncio.run(
        client.request_json(
            method="GET",
            url="https://example.test/data",
            gate=gate,
            max_pause_wait_seconds=20,
        )
    )

    assert result.status == 429
    assert result.text == "service paused"
    assert result.headers["retry-after"] == "1800"
    assert transport.requests == 0


def test_short_retry_after_still_retries_inline(monkeypatch):
    transport = _FakeHTTPXClient([_rate_limited(2), _response()])
    gate = _FakeGate()
    sleeps: list[float] = []
    client = HTTPClient(timeout_seconds=5, max_retries=3, client_factory=lambda: transport)

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("betterer_ratings.infra.http.client.asyncio.sleep", record_sleep)

    result = asyncio.run(
        client.request_json(
            method="GET",
            url="https://example.test/data",
            gate=gate,
            max_pause_wait_seconds=20,
        )
    )

    assert result.status == 200
    assert transport.requests == 2
    assert sleeps == [2]


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_repeated_read_or_protocol_errors_recycle_pool_and_log_details(
    monkeypatch, caplog, error_type
):
    request = httpx.Request("GET", "https://example.test/data")
    stale = _FakeHTTPXClient(
        [
            error_type("first stale connection", request=request),
            error_type("second stale connection", request=request),
        ]
    )
    fresh = _FakeHTTPXClient([_response()])
    clients = iter([stale, fresh])
    client = HTTPClient(
        timeout_seconds=5,
        max_retries=3,
        client_factory=lambda: next(clients),
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("betterer_ratings.infra.http.client.asyncio.sleep", no_sleep)

    with caplog.at_level(logging.INFO, logger="betterer-ratings"):
        result = asyncio.run(
            client.request_json(method="GET", url="https://example.test/data")
        )

    assert result.status == 200
    assert stale.closed == 1
    assert stale.requests == 2
    assert fresh.requests == 1
    recycle_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "http.connection_pool_recycled"
    )
    assert recycle_record.error_type == error_type.__name__
    assert error_type.__name__ in recycle_record.getMessage()
    assert "second stale connection" in recycle_record.error_repr


def _counts(**overrides):
    counts = {
        "ratings_pending": 1,
        "ratings_in_flight": 1,
        "ratings_failed": 0,
        "mappings_pending": 1,
        "mappings_in_flight": 1,
        "mappings_failed": 0,
        "episode_ratings_pending": 0,
        "episode_ratings_in_flight": 0,
        "episode_ratings_failed": 0,
    }
    counts.update(overrides)
    return counts


def _summary(ratings=10, mappings=10, episodes=0):
    return {
        "day": "2026-08-12",
        "ratings_today": ratings,
        "mappings_today": mappings,
        "episode_ratings_today": episodes,
    }


def _due_counts(**overrides):
    counts = {
        "ratings_due": 1,
        "mappings_due": 1,
        "episode_ratings_due": 0,
    }
    counts.update(overrides)
    return counts


def test_queue_alerts_only_after_sustained_stall_and_recovers_on_progress():
    monitor = QueueAlertMonitor(stall_threshold_seconds=300, stall_repeat_seconds=900)

    assert monitor.observe(
        now_ts=1000,
        counts=_counts(),
        due_counts=_due_counts(),
        summary=_summary(),
    ) == []
    assert monitor.observe(
        now_ts=1299,
        counts=_counts(),
        due_counts=_due_counts(),
        summary=_summary(),
    ) == []

    stalled = monitor.observe(
        now_ts=1300,
        counts=_counts(),
        due_counts=_due_counts(),
        summary=_summary(),
    )
    assert [event["event"] for event in stalled] == ["queue.alert.stalled"]
    assert stalled[0]["stalled_seconds"] == 300

    assert monitor.observe(
        now_ts=1400,
        counts=_counts(),
        due_counts=_due_counts(),
        summary=_summary(),
    ) == []
    recovered = monitor.observe(
        now_ts=1401,
        counts=_counts(),
        due_counts=_due_counts(),
        summary=_summary(ratings=11),
    )
    assert [event["event"] for event in recovered] == ["queue.alert.recovered"]


def test_queue_alerts_ignore_retries_that_are_not_due():
    monitor = QueueAlertMonitor(stall_threshold_seconds=300, stall_repeat_seconds=900)
    future_only_counts = _counts(
        ratings_pending=1,
        ratings_in_flight=0,
        mappings_pending=0,
        mappings_in_flight=0,
    )
    nothing_due = _due_counts(ratings_due=0, mappings_due=0, episode_ratings_due=0)

    assert monitor.observe(
        now_ts=1000,
        counts=future_only_counts,
        due_counts=nothing_due,
        summary=_summary(),
    ) == []
    assert monitor.observe(
        now_ts=1300,
        counts=future_only_counts,
        due_counts=nothing_due,
        summary=_summary(),
    ) == []


def test_queue_alerts_immediately_when_failed_rows_are_nonzero():
    monitor = QueueAlertMonitor()

    events = monitor.observe(
        now_ts=1000,
        counts=_counts(ratings_failed=2, mappings_failed=1),
        due_counts=_due_counts(),
        summary=_summary(),
    )

    assert [event["event"] for event in events] == ["queue.alert.failed"]
    assert events[0]["failed_total"] == 3
