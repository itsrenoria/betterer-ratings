"""Regression coverage for PMDB 401 responses on the delete leg.

PMDB's `validateApiKey` answers 401 when PMDB's own upstream admin token is
missing or stale, so a 401 is a transient server-side condition -- not a
rejection of our API key. Ownership failures are 403 and unknown rating ids
are 404, both of which keep their existing handling.

`to_delete_result` previously had no 401 branch, so a 401 fell through to the
non-retryable default and permanently failed the row -- even though
`to_submit_result` treats 401 as retryable and the transport pauses both PMDB
gates for 300s on the very same response. These tests pin the delete leg to
the same 300s backoff, end to end.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from betterer_ratings.core.parsing import first_non_empty
from betterer_ratings.domain.models import APIResponse
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.providers.pmdb_submission_rating import submit_rating
from betterer_ratings.services.submit import handler_rating
from betterer_ratings.services.submit.retry_policy import (
    format_manual_error,
    format_pmdb_error,
    retry_delay_seconds,
)

CACHED_RATING_ID = "phuyt1pht9wz4za"
INVALID_KEY_DATA = {"error": "Invalid API key"}
INVALID_KEY_TEXT = '{"error":"Invalid API key"}'
OWNERSHIP_DENIAL_TEXT = '{"error":"Access denied - you can only delete your own data"}'


def _response(*, status: int, headers=None, data=None, text: str = "") -> APIResponse:
    return APIResponse(status=status, headers=headers or {}, data=data, text=text)


def _unauthorized() -> APIResponse:
    return _response(
        status=401,
        headers={"content-type": "application/json"},
        data=INVALID_KEY_DATA,
        text=INVALID_KEY_TEXT,
    )


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _FakeGate:
    name = "pmdb"

    def __init__(self):
        self.paused: list[tuple[int, str]] = []

    async def acquire(self, max_pause_wait_seconds=None):
        return True

    def observe_headers(self, headers, status):
        pass

    def pause_for(self, seconds, reason):
        self.paused.append((seconds, reason))


class _FakeHTTP:
    """Replays one response per HTTP method and records what was requested."""

    def __init__(self, by_method: dict[str, APIResponse]):
        self._by_method = by_method
        self.calls: list[tuple[str, str]] = []

    async def request_json(self, *, method, url, params=None, headers=None,
                           json_body=None, gate=None, max_pause_wait_seconds=None):
        self.calls.append((method, url))
        try:
            return self._by_method[method]
        except KeyError:  # pragma: no cover - signals a mis-specified scenario
            raise AssertionError(f"unexpected {method} {url}") from None

    async def aclose(self):
        pass


class _FakePMDBConfig:
    base_url = "https://publicmetadb.com"
    timeout_seconds = 30
    max_retries = 3


def _client(by_method: dict[str, APIResponse]) -> PMDBClient:
    client = PMDBClient(
        api_key="unused-in-tests",
        config=_FakePMDBConfig(),
        api_gate=_FakeGate(),
        rating_gate=_FakeGate(),
        mapping_gate=_FakeGate(),
        logger=_NullLogger(),
    )
    client.http = _FakeHTTP(by_method)
    return client


class _FakeRatingDB:
    def __init__(self):
        self.submitted: list[tuple] = []
        self.retried: list[tuple] = []
        self.failed: list[tuple] = []
        self.cleared: list[tuple] = []

    def mark_rating_submitted(self, tmdb_id, media_type, label, submitted_at, pmdb_item_id=None):
        self.submitted.append((tmdb_id, media_type, label, submitted_at, pmdb_item_id))

    def mark_rating_retry(self, tmdb_id, media_type, label, retry_after, error_text):
        self.retried.append((tmdb_id, media_type, label, retry_after, error_text))

    def mark_rating_failed(self, tmdb_id, media_type, label, error_text):
        self.failed.append((tmdb_id, media_type, label, error_text))

    def clear_rating_pmdb_item_id(self, tmdb_id, media_type, label):
        self.cleared.append((tmdb_id, media_type, label))


def _run_handler(
    *,
    client: PMDBClient,
    db: _FakeRatingDB,
    cached_id: Optional[str] = CACHED_RATING_ID,
    attempts: int = 0,
) -> None:
    asyncio.run(
        handler_rating.submit_rating(
            row={
                "tmdb_id": 24679,
                "media_type": "movie",
                "label": "IM",
                "score": 72.0,
                "pmdb_item_id": cached_id,
                "pmdb_attempts": attempts,
            },
            pmdb_client=client,
            db=db,
            verify_after_transient_statuses=set(),
            max_retry_attempts=5,
            format_manual_error_fn=format_manual_error,
            format_pmdb_error_fn=format_pmdb_error,
            retry_delay_seconds_fn=lambda result, current_attempts: retry_delay_seconds(
                retry_after_seconds=int(result.retry_after_seconds or 30),
                current_attempts=current_attempts,
            ),
            now_epoch_fn=lambda: 1_000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )


def test_to_delete_result_treats_401_as_retryable_with_300s_backoff():
    result = PMDBClient._to_delete_result(
        _unauthorized(),
        endpoint=f"/api/external/ratings/{CACHED_RATING_ID}",
    )

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after_seconds == 300


def test_to_delete_result_401_preserves_status_code_message_and_endpoint():
    endpoint = f"/api/external/ratings/{CACHED_RATING_ID}"
    result = PMDBClient._to_delete_result(_unauthorized(), endpoint=endpoint)

    assert result.status_code == 401
    assert result.error_code == "Invalid API key"
    assert result.error_text == INVALID_KEY_TEXT
    assert result.endpoint == endpoint


def test_to_delete_result_401_matches_the_submit_leg():
    """The two legs must agree: a 401 is transient for both."""
    delete_result = PMDBClient._to_delete_result(_unauthorized())
    submit_result = PMDBClient._to_submit_result(_unauthorized())

    assert delete_result.retryable == submit_result.retryable
    assert delete_result.retry_after_seconds == submit_result.retry_after_seconds


def test_delete_403_ownership_and_404_handling_are_unchanged():
    ownership = PMDBClient._to_delete_result(
        _response(status=403, data={"error": "forbidden"}, text=OWNERSHIP_DENIAL_TEXT),
    )
    assert ownership.success is False
    assert ownership.retryable is False
    assert ownership.retry_after_seconds == 0

    missing = PMDBClient._to_delete_result(_response(status=404, text="missing"))
    assert missing.success is True
    assert missing.retryable is False


def test_submit_rating_returns_retryable_300s_when_cached_id_delete_is_401():
    client = _client({"DELETE": _unauthorized()})

    result = asyncio.run(
        submit_rating(
            client,
            tmdb_id=24679,
            media_type="movie",
            label="IM",
            score=72.0,
            existing_pmdb_item_id=CACHED_RATING_ID,
        )
    )

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after_seconds == 300
    assert result.status_code == 401
    assert result.endpoint == f"/api/external/ratings/{CACHED_RATING_ID}"
    # A 401 is not the ownership signal, so the cached id stays trusted.
    assert result.stale_cached_item_id is False
    # The submission short-circuits on the failed delete; no create is attempted.
    assert client.http.calls == [
        ("DELETE", f"https://publicmetadb.com/api/external/ratings/{CACHED_RATING_ID}")
    ]


def test_handler_schedules_retry_instead_of_failing_row_on_delete_401():
    client = _client({"DELETE": _unauthorized()})
    db = _FakeRatingDB()

    _run_handler(client=client, db=db)

    assert db.failed == []
    assert db.submitted == []
    assert len(db.retried) == 1
    # First attempt: 300s base with no exponential multiplier yet.
    assert db.retried[0][3] == 1_000 + 300
    assert '"status":401' in db.retried[0][4]
    assert '"retryable":true' in db.retried[0][4]
    assert f"/api/external/ratings/{CACHED_RATING_ID}" in db.retried[0][4]
    # 401 is not an ownership failure, so the cached id must not be cleared.
    assert db.cleared == []


def test_delete_401_backoff_grows_across_attempts():
    client = _client({"DELETE": _unauthorized()})
    db = _FakeRatingDB()

    _run_handler(client=client, db=db, attempts=2)

    assert len(db.retried) == 1
    # retry_delay_seconds doubles per attempt: 300 * 2 ** (3 - 1).
    assert db.retried[0][3] == 1_000 + 1_200


def test_delete_401_pauses_both_pmdb_gates_for_300s():
    client = _client({"DELETE": _unauthorized()})
    api_gate: Any = client.api_gate
    rating_gate: Any = client.rating_gate

    asyncio.run(client._delete_rating_by_id(CACHED_RATING_ID))

    assert api_gate.paused == [(300, "PMDB unauthorized")]
    assert rating_gate.paused == [(300, "PMDB unauthorized")]
