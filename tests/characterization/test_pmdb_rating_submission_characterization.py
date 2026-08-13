from __future__ import annotations

import asyncio
from typing import Optional

from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult, PMDBSubmitResult
from betterer_ratings.providers.pmdb_submission_rating import submit_rating


class _StaleRatingIdClient:
    base_url = "https://pmdb.example"
    rating_gate = object()

    def __init__(self):
        self.deleted_ids: list[str] = []
        self.posts: list[dict[str, object]] = []

    async def _delete_rating_by_id(self, rating_id: str) -> PMDBDeleteResult:
        self.deleted_ids.append(rating_id)
        return PMDBDeleteResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            error_text='{"error":"Access denied - you can only delete your own data"}',
            status_code=403,
            error_code="Access denied - you can only delete your own data",
            endpoint=f"/api/external/ratings/{rating_id}",
        )

    async def _post_with_gates(
        self,
        *,
        url: str,
        payload: dict[str, object],
        contribution_gate: object,
    ) -> APIResponse:
        self.posts.append(payload)
        return APIResponse(
            status=201,
            headers={},
            data={"id": "new-rating-id"},
            text='{"id":"new-rating-id"}',
        )

    def _to_submit_result(self, response: APIResponse, endpoint: str = "") -> PMDBSubmitResult:
        return PMDBSubmitResult(
            success=True,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text="",
            item_id=response.data["id"],
            status_code=response.status,
            endpoint=endpoint,
        )

    def _is_create_failed_rating(self, result: PMDBSubmitResult) -> bool:
        return False

    def _is_duplicate_or_exists_result(self, result: PMDBSubmitResult) -> bool:
        return False


def test_submit_rating_recreates_when_cached_pmdb_id_is_not_owned():
    client = _StaleRatingIdClient()

    result = asyncio.run(
        submit_rating(
            client,
            tmdb_id=936370,
            media_type="movie",
            label="IM",
            score=63.0,
            existing_pmdb_item_id="stale-rating-id",
        )
    )

    assert result.success is True
    assert result.item_id == "new-rating-id"
    assert client.deleted_ids == ["stale-rating-id"]
    assert client.posts == [
        {
            "tmdb_id": 936370,
            "media_type": "movie",
            "score": 63.0,
            "label": "IM",
        }
    ]
    assert result.stale_cached_item_id is True


class _StaleRatingIdThenFailingPostClient(_StaleRatingIdClient):
    """Same not-owned delete, but the recreate POST also fails (non-duplicate)."""

    async def _post_with_gates(
        self,
        *,
        url: str,
        payload: dict[str, object],
        contribution_gate: object,
    ) -> APIResponse:
        self.posts.append(payload)
        return APIResponse(
            status=500,
            headers={},
            data={"error": "some other failure"},
            text='{"error":"some other failure"}',
        )

    def _to_submit_result(self, response: APIResponse, endpoint: str = "") -> PMDBSubmitResult:
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=30,
            duplicate_or_exists=False,
            error_text=response.text,
            item_id=None,
            status_code=response.status,
            endpoint=endpoint,
        )


def test_submit_rating_flags_stale_cached_id_even_when_recreate_fails():
    """Regression: a not-owned 403 must be flagged so callers can stop trusting the
    cached id, even when the follow-up recreate attempt does not succeed either."""
    client = _StaleRatingIdThenFailingPostClient()

    result = asyncio.run(
        submit_rating(
            client,
            tmdb_id=936370,
            media_type="movie",
            label="IM",
            score=63.0,
            existing_pmdb_item_id="stale-rating-id",
        )
    )

    assert result.success is False
    assert result.stale_cached_item_id is True
    assert client.deleted_ids == ["stale-rating-id"]


class _StaleRatingIdThenDuplicateClient(_StaleRatingIdClient):
    """Not-owned delete, followed by a create that PMDB reports as a duplicate,
    routing through the replace-after-duplicate resolution path."""

    def __init__(self):
        super().__init__()
        self.replace_calls: list[dict[str, object]] = []

    async def _post_with_gates(
        self,
        *,
        url: str,
        payload: dict[str, object],
        contribution_gate: object,
    ) -> APIResponse:
        self.posts.append(payload)
        return APIResponse(
            status=409,
            headers={},
            data={"error": "duplicate"},
            text='{"error":"duplicate"}',
        )

    def _to_submit_result(self, response: APIResponse, endpoint: str = "") -> PMDBSubmitResult:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=True,
            error_text=response.text,
            item_id=None,
            status_code=response.status,
            error_code="duplicate",
            endpoint=endpoint,
        )

    def _is_create_failed_rating(self, result: PMDBSubmitResult) -> bool:
        return False

    def _is_duplicate_or_exists_result(self, result: PMDBSubmitResult) -> bool:
        return True

    async def _replace_rating_after_duplicate(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        label: str,
        score: float,
        known_item_id: Optional[str],
    ) -> PMDBSubmitResult:
        self.replace_calls.append(
            {
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "label": label,
                "score": score,
                "known_item_id": known_item_id,
            }
        )
        # Mirrors the real replace_rating_after_duplicate(): it has no notion
        # of the earlier not-owned delete, so it never sets the flag itself.
        return PMDBSubmitResult(
            success=True,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=True,
            error_text="",
            item_id="resolved-duplicate-id",
            status_code=200,
            error_code="exists",
            endpoint="/api/external/ratings",
        )


def test_submit_rating_propagates_stale_cached_id_through_duplicate_resolution():
    """Regression: when the initial not-owned delete is followed by a duplicate
    create response, the duplicate-resolution path must still result in
    stale_cached_item_id=True on the final returned result."""
    client = _StaleRatingIdThenDuplicateClient()

    result = asyncio.run(
        submit_rating(
            client,
            tmdb_id=936370,
            media_type="movie",
            label="IM",
            score=63.0,
            existing_pmdb_item_id="stale-rating-id",
        )
    )

    assert client.deleted_ids == ["stale-rating-id"]
    assert len(client.replace_calls) == 1
    assert client.replace_calls[0]["known_item_id"] == "stale-rating-id"
    assert result.success is True
    assert result.item_id == "resolved-duplicate-id"
    assert result.stale_cached_item_id is True
