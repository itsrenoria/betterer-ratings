from __future__ import annotations

import asyncio

import pytest

from betterer_ratings.core.parsing import first_non_empty, parse_int
from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult, PMDBSubmitResult
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.services.submit.handler_episode_batch import (
    submit_episode_ratings_batch,
)


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


class _DB:
    def __init__(self):
        self.submitted = []
        self.retried = []
        self.failed = []
        self.cleared = []

    def mark_episode_rating_submitted(self, *args, **kwargs):
        self.submitted.append((args, kwargs))

    def mark_episode_rating_retry(self, *args):
        self.retried.append(args)

    def mark_episode_rating_failed(self, *args):
        self.failed.append(args)

    def clear_episode_rating_pmdb_item_id(self, *args):
        self.cleared.append(args)


def _row(episode: int, item_id: str | None):
    return {
        "tmdb_id": 10,
        "media_type": "tv",
        "season": 1,
        "label": "IM",
        "episode": episode,
        "pmdb_item_id": item_id,
        "score": 80.0 + episode,
        "pmdb_attempts": 0,
    }


def _delete_result(
    *,
    success: bool,
    status: int,
    retryable: bool = False,
    text: str = "",
    code: str = "",
):
    return PMDBDeleteResult(
        success=success,
        retryable=retryable,
        retry_after_seconds=30 if retryable else 0,
        error_text=text,
        status_code=status,
        error_code=code,
        endpoint="/api/external/episode-ratings/id",
    )


class _Client:
    def __init__(self, batch_delete, individual=None):
        self.batch_delete = batch_delete
        self.individual = individual or {}
        self.batch_delete_calls = []
        self.individual_calls = []
        self.submit_calls = []

    async def delete_episode_ratings_batch(self, rating_ids):
        self.batch_delete_calls.append(list(rating_ids))
        return self.batch_delete

    async def delete_episode_rating_by_id(self, rating_id):
        self.individual_calls.append(rating_id)
        return self.individual[rating_id]

    async def submit_episode_ratings_batch(self, **kwargs):
        self.submit_calls.append(kwargs)
        return APIResponse(
            status=200,
            headers={},
            data={
                "items": [
                    {"episode": item["episode"], "id": f"new-{item['episode']}"}
                    for item in kwargs["ratings"]
                ]
            },
            text="",
        )

    async def confirm_episode_rating_exists(self, **_kwargs):
        return False, None


def _run(rows, client, db):
    asyncio.run(
        submit_episode_ratings_batch(
            rows=rows,
            pmdb_client=client,
            db=db,
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: str(kwargs),
            retry_delay_seconds_fn=lambda result, _attempts: result.retry_after_seconds,
            now_epoch_fn=lambda: 1_000,
            first_non_empty_fn=first_non_empty,
            parse_int_fn=parse_int,
            parse_retry_after_fn=parse_retry_after,
            pmdb_submit_result_cls=PMDBSubmitResult,
            extract_error_code_fn=PMDBClient._extract_error_code,
            is_cloudflare_challenge_fn=PMDBClient._is_cloudflare_challenge,
            logger=_Logger(),
        )
    )


def test_batch_delete_uses_deleted_ids_as_authoritative_success():
    rows = [_row(1, "old-1"), _row(2, "old-2")]
    client = _Client(
        APIResponse(
            status=200,
            headers={},
            data={"deletedIds": ["old-1", "old-2"]},
            text="",
        )
    )
    db = _DB()

    _run(rows, client, db)

    assert client.batch_delete_calls == [["old-1", "old-2"]]
    assert client.individual_calls == []
    assert len(client.submit_calls) == 1
    assert len(db.submitted) == 2


def test_batch_delete_207_falls_back_only_for_unresolved_ids():
    rows = [_row(1, "old-1"), _row(2, "old-2")]
    client = _Client(
        APIResponse(
            status=207,
            headers={},
            data={"deletedIds": ["old-1"]},
            text="partial",
        ),
        {"old-2": _delete_result(success=True, status=200)},
    )
    db = _DB()

    _run(rows, client, db)

    assert client.individual_calls == ["old-2"]
    assert len(client.submit_calls[0]["ratings"]) == 2


@pytest.mark.parametrize("status", [0, 401, 429, 500, 503])
def test_transient_batch_delete_does_not_fan_out_or_create(status):
    row = _row(1, "old-1")
    client = _Client(
        APIResponse(
            status=status,
            headers={"retry-after": "3600"},
            data={"error": "rate limited"},
            text="rate limited",
        )
    )
    db = _DB()

    _run([row], client, db)

    assert client.individual_calls == []
    assert client.submit_calls == []
    assert len(db.retried) == 1
    expected_retry_at = 4_600 if status == 429 else (1_300 if status == 401 else 1_030)
    assert db.retried[0][-2] == expected_retry_at


def test_malformed_batch_delete_falls_back_to_individual_delete():
    row = _row(1, "old-1")
    client = _Client(
        APIResponse(status=200, headers={}, data={"unexpected": True}, text=""),
        {"old-1": _delete_result(success=True, status=204)},
    )
    db = _DB()

    _run([row], client, db)

    assert client.individual_calls == ["old-1"]
    assert len(client.submit_calls) == 1


def test_challenge_batch_delete_does_not_fan_out_or_create():
    client = _Client(
        APIResponse(
            status=403,
            headers={"cf-mitigated": "challenge", "content-type": "text/html"},
            data=None,
            text="challenge",
        )
    )
    db = _DB()

    _run([_row(1, "old-1")], client, db)

    assert client.individual_calls == []
    assert client.submit_calls == []
    assert len(db.retried) == 1
    assert db.retried[0][-2] == 1_300


def test_exact_unowned_id_is_cleared_then_desired_rating_is_created():
    row = _row(1, "foreign-1")
    client = _Client(
        APIResponse(status=200, headers={}, data={"deletedIds": []}, text=""),
        {
            "foreign-1": _delete_result(
                success=False,
                status=403,
                text="Access denied - you can only delete your own data",
                code="forbidden",
            )
        },
    )
    db = _DB()

    _run([row], client, db)

    assert db.cleared == [(10, "tv", 1, 1, "IM")]
    assert len(client.submit_calls) == 1
    assert len(db.submitted) == 1
