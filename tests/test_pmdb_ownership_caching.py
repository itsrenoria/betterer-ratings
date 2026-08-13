"""Regression coverage for PMDB ownership/cached-id handling.

These tests guard against the 403 "you can only delete your own data" bug:
a `pmdb_item_id` must never be cached from an unauthenticated, label/value
matched lookup (`confirm_rating_exists` / `confirm_mapping_exists`), and a
cached id PMDB has confirmed we don't own must be cleared.
"""

from __future__ import annotations

import asyncio

from betterer_ratings.core.parsing import first_non_empty
from betterer_ratings.domain.models import PMDBSubmitResult
from betterer_ratings.services.submit import handler_mapping, handler_rating


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


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _row(**overrides):
    base = {
        "tmdb_id": 42,
        "media_type": "movie",
        "label": "IM",
        "score": 70.0,
        "pmdb_item_id": None,
        "pmdb_attempts": 0,
    }
    base.update(overrides)
    return base


def test_confirmed_transient_match_does_not_cache_matched_rating_id():
    """`confirm_rating_exists` matches by label+score only (no owner check), so a
    hit must not be trusted as our own id for future deletes."""

    class FakePMDB:
        async def submit_rating(self, **kwargs):
            return PMDBSubmitResult(
                success=False,
                retryable=True,
                retry_after_seconds=30,
                duplicate_or_exists=False,
                error_text="server error",
                item_id=None,
                status_code=500,
            )

        async def confirm_rating_exists(self, **kwargs):
            return True, "someone-elses-rating-id"

    db = _FakeRatingDB()
    asyncio.run(
        handler_rating.submit_rating(
            row=_row(pmdb_item_id=None),
            pmdb_client=FakePMDB(),
            db=db,
            verify_after_transient_statuses={500},
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: "manual-error",
            format_pmdb_error_fn=lambda **kwargs: "pmdb-error",
            retry_delay_seconds_fn=lambda result, attempts: 60,
            now_epoch_fn=lambda: 1000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )

    assert len(db.submitted) == 1
    _, _, _, _, cached_id = db.submitted[0]
    assert cached_id is None, "matched-but-unowned id must never be cached"


def test_confirmed_transient_match_preserves_previously_owned_id():
    """If we already had a legitimately-owned id cached, a transient-failure
    confirmation must not overwrite it with an unverified matched id."""

    class FakePMDB:
        async def submit_rating(self, **kwargs):
            return PMDBSubmitResult(
                success=False,
                retryable=True,
                retry_after_seconds=30,
                duplicate_or_exists=False,
                error_text="server error",
                item_id=None,
                status_code=500,
            )

        async def confirm_rating_exists(self, **kwargs):
            return True, "some-other-matched-id"

    db = _FakeRatingDB()
    asyncio.run(
        handler_rating.submit_rating(
            row=_row(pmdb_item_id="our-real-id"),
            pmdb_client=FakePMDB(),
            db=db,
            verify_after_transient_statuses={500},
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: "manual-error",
            format_pmdb_error_fn=lambda **kwargs: "pmdb-error",
            retry_delay_seconds_fn=lambda result, attempts: 60,
            now_epoch_fn=lambda: 1000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )

    assert db.submitted[0][4] == "our-real-id"


def test_not_owned_delete_clears_stale_cached_id_and_does_not_retry_delete_it():
    """When the client signals `stale_cached_item_id`, the handler must clear the
    cached id so future cycles stop trying to delete a foreign entry."""

    class FakePMDB:
        async def submit_rating(self, **kwargs):
            return PMDBSubmitResult(
                success=False,
                retryable=True,
                retry_after_seconds=30,
                duplicate_or_exists=False,
                error_text="some other failure",
                item_id=None,
                status_code=500,
                stale_cached_item_id=True,
            )

        async def confirm_rating_exists(self, **kwargs):
            return False, None

    db = _FakeRatingDB()
    asyncio.run(
        handler_rating.submit_rating(
            row=_row(pmdb_item_id="foreign-rating-id"),
            pmdb_client=FakePMDB(),
            db=db,
            verify_after_transient_statuses=set(),
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: "manual-error",
            format_pmdb_error_fn=lambda **kwargs: "pmdb-error",
            retry_delay_seconds_fn=lambda result, attempts: 60,
            now_epoch_fn=lambda: 1000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )

    assert db.cleared == [(42, "movie", "IM")]
    assert len(db.retried) == 1


class _FakeMappingDB:
    def __init__(self):
        self.submitted: list[tuple] = []

    def mark_mapping_submitted(self, tmdb_id, media_type, id_type, submitted_at, pmdb_item_id=None):
        self.submitted.append((tmdb_id, media_type, id_type, submitted_at, pmdb_item_id))

    def mark_mapping_retry(self, *args, **kwargs):
        pass

    def mark_mapping_failed(self, *args, **kwargs):
        pass


def test_confirmed_transient_match_does_not_cache_matched_mapping_id():
    """Same protection for mappings: `confirm_mapping_exists` matches by
    id_type+id_value against an unauthenticated lookup, so a hit must not be
    cached as our own id."""

    class FakePMDB:
        async def submit_mapping(self, **kwargs):
            return PMDBSubmitResult(
                success=False,
                retryable=True,
                retry_after_seconds=30,
                duplicate_or_exists=False,
                error_text="server error",
                item_id=None,
                status_code=500,
            )

        async def confirm_mapping_exists(self, **kwargs):
            return True, "someone-elses-mapping-id"

    db = _FakeMappingDB()
    asyncio.run(
        handler_mapping.submit_mapping(
            row={
                "tmdb_id": 42,
                "media_type": "movie",
                "id_type": "imdb",
                "id_value": "tt123",
                "pmdb_item_id": None,
                "pmdb_attempts": 0,
            },
            pmdb_client=FakePMDB(),
            db=db,
            verify_after_transient_statuses={500},
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: "manual-error",
            format_pmdb_error_fn=lambda **kwargs: "pmdb-error",
            retry_delay_seconds_fn=lambda result, attempts: 60,
            now_epoch_fn=lambda: 1000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )

    assert len(db.submitted) == 1
    assert db.submitted[0][4] is None, "matched-but-unowned mapping id must never be cached"
