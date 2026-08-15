from __future__ import annotations

import asyncio

from betterer_ratings.core.parsing import first_non_empty
from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult, PMDBSubmitResult
from betterer_ratings.providers.pmdb_submission_mapping import submit_mapping
from betterer_ratings.services.submit import handler_mapping

OWNERSHIP_DENIAL = "Access denied - you can only delete your own data"


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _MappingClient:
    base_url = "https://pmdb.example"
    mapping_gate = object()

    def __init__(self, delete_result: PMDBDeleteResult):
        self.delete_result = delete_result
        self.deleted_ids: list[str] = []
        self.posts: list[dict[str, object]] = []

    async def _delete_mapping_by_id(self, mapping_id: str) -> PMDBDeleteResult:
        self.deleted_ids.append(mapping_id)
        return self.delete_result

    async def _post_with_gates(self, *, url, payload, contribution_gate):
        self.posts.append(payload)
        return APIResponse(
            status=201,
            headers={},
            data={"item": {"id": "new-mapping-id"}},
            text='{"item":{"id":"new-mapping-id"}}',
        )

    @staticmethod
    def _to_submit_result(response, endpoint=""):
        return PMDBSubmitResult(
            success=True,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text="",
            item_id="new-mapping-id",
            status_code=response.status,
            endpoint=endpoint,
        )

    @staticmethod
    def _is_create_failed_mapping(result):
        return False

    @staticmethod
    def _is_duplicate_or_exists_result(result):
        return False


def _delete_result(*, success=False, retryable=False, status=403, text=OWNERSHIP_DENIAL):
    return PMDBDeleteResult(
        success=success,
        retryable=retryable,
        retry_after_seconds=300 if retryable else 0,
        error_text=text,
        status_code=status,
        error_code=text,
        endpoint="/api/external/mappings/stale-mapping-id",
    )


def test_mapping_replacement_continues_after_exact_ownership_denial():
    client = _MappingClient(_delete_result())

    result = asyncio.run(
        submit_mapping(
            client,
            tmdb_id=10,
            media_type="movie",
            id_type="tvdb",
            id_value="new",
            existing_pmdb_item_id="stale-mapping-id",
        )
    )

    assert result.success is True
    assert result.item_id == "new-mapping-id"
    assert result.stale_cached_item_id is True
    assert client.deleted_ids == ["stale-mapping-id"]
    assert client.posts == [
        {"tmdb_id": 10, "media_type": "movie", "id_type": "tvdb", "id_value": "new"}
    ]


def test_mapping_replacement_invalidates_successfully_deleted_cached_id():
    client = _MappingClient(
        _delete_result(success=True, status=204, text="")
    )

    result = asyncio.run(
        submit_mapping(
            client,
            tmdb_id=10,
            media_type="movie",
            id_type="tvdb",
            id_value="new",
            existing_pmdb_item_id="old-owned-mapping-id",
        )
    )

    assert result.success is True
    assert result.stale_cached_item_id is True
    assert client.deleted_ids == ["old-owned-mapping-id"]
    assert len(client.posts) == 1


def test_mapping_replacement_stops_when_delete_failure_is_not_ownership_denial():
    client = _MappingClient(
        _delete_result(retryable=True, status=429, text="rate limited")
    )

    result = asyncio.run(
        submit_mapping(
            client,
            tmdb_id=10,
            media_type="movie",
            id_type="tvdb",
            id_value="new",
            existing_pmdb_item_id="stale-mapping-id",
        )
    )

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after_seconds == 300
    assert result.stale_cached_item_id is False
    assert client.posts == []


class _HandlerDB:
    def __init__(self):
        self.cleared: list[tuple] = []
        self.submitted: list[tuple] = []

    def clear_mapping_pmdb_item_id(self, tmdb_id, media_type, id_type):
        self.cleared.append((tmdb_id, media_type, id_type))

    def mark_mapping_submitted(
        self, tmdb_id, media_type, id_type, submitted_at, pmdb_item_id=None
    ):
        self.submitted.append((tmdb_id, media_type, id_type, submitted_at, pmdb_item_id))

    def mark_mapping_retry(self, *args, **kwargs):
        raise AssertionError("unexpected retry")

    def mark_mapping_failed(self, *args, **kwargs):
        raise AssertionError("unexpected failure")


class _HandlerPMDB:
    def __init__(self):
        self.existing_ids: list[str | None] = []

    async def submit_mapping(self, *, existing_pmdb_item_id=None, **kwargs):
        self.existing_ids.append(existing_pmdb_item_id)
        return PMDBSubmitResult(
            success=True,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text="",
            item_id="new-mapping-id",
            status_code=201,
            stale_cached_item_id=True,
        )


def test_mapping_handler_clears_invalidated_cache_and_stores_only_new_create_id():
    db = _HandlerDB()
    pmdb = _HandlerPMDB()

    asyncio.run(
        handler_mapping.submit_mapping(
            row={
                "tmdb_id": 10,
                "media_type": "movie",
                "id_type": "tvdb",
                "id_value": "new",
                "pmdb_item_id": "stale-mapping-id",
                "pmdb_attempts": 0,
            },
            pmdb_client=pmdb,
            db=db,
            verify_after_transient_statuses=set(),
            max_retry_attempts=5,
            format_manual_error_fn=lambda **kwargs: "manual",
            format_pmdb_error_fn=lambda **kwargs: "pmdb",
            retry_delay_seconds_fn=lambda result, attempts: 30,
            now_epoch_fn=lambda: 1000,
            first_non_empty_fn=first_non_empty,
            logger=_NullLogger(),
        )
    )

    assert pmdb.existing_ids == ["stale-mapping-id"]
    assert db.cleared == [(10, "movie", "tvdb")]
    assert db.submitted == [(10, "movie", "tvdb", 1000, "new-mapping-id")]
