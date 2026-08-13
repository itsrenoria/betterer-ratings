from __future__ import annotations

from dataclasses import replace
from typing import Any, List, Optional, cast

from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.domain.models import PMDBDeleteResult, PMDBSubmitResult


async def delete_rating_by_id(client: Any, rating_id: str) -> PMDBDeleteResult:
    response = await client._delete_with_gates(
        url=f"{client.base_url}/api/external/ratings/{rating_id}",
        contribution_gate=client.rating_gate,
    )
    return cast(PMDBDeleteResult, client._to_delete_result(
        response,
        endpoint=f"/api/external/ratings/{rating_id}",
    ))


def _is_not_owned_delete_failure(delete_result: PMDBDeleteResult) -> bool:
    text = f"{delete_result.error_code} {delete_result.error_text}".lower()
    return int(delete_result.status_code or 0) == 403 and "only delete your own data" in text


async def replace_rating_after_duplicate(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    score: float,
    known_item_id: Optional[str],
) -> PMDBSubmitResult:
    lookup = await client._fetch_existing_ratings(tmdb_id, media_type)
    if lookup.status in (429, 500, 502, 503, 504, 0):
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=parse_retry_after(lookup.headers.get("retry-after"), 30),
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB rating lookup failed",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/ratings",
        )
    if lookup.status == 401:
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=300,
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB unauthorized while listing ratings",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/ratings",
        )
    if lookup.status == 403:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB forbidden while listing ratings",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/ratings",
        )

    entries = client._extract_ratings_for_label(lookup.data, label)
    candidate_ids: List[str] = []
    if known_item_id:
        candidate_ids.append(known_item_id)
    for entry in entries:
        entry_id = client._extract_entry_id(entry)
        if entry_id and entry_id not in candidate_ids:
            candidate_ids.append(entry_id)

    deleted_any = False
    for entry_id in candidate_ids:
        delete_result = await client._delete_rating_by_id(entry_id)
        if delete_result.retryable:
            return PMDBSubmitResult(
                success=False,
                retryable=True,
                retry_after_seconds=delete_result.retry_after_seconds,
                duplicate_or_exists=False,
                error_text=delete_result.error_text,
                item_id=None,
                status_code=delete_result.status_code,
                error_code=delete_result.error_code,
                endpoint=delete_result.endpoint or "/api/external/ratings",
            )
        if delete_result.success:
            deleted_any = True

    if deleted_any:
        retry_response = await client._post_with_gates(
            url=f"{client.base_url}/api/external/ratings",
            payload={
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "score": score,
                "label": label,
            },
            contribution_gate=client.rating_gate,
        )
        retry_result = client._to_submit_result(
            retry_response,
            endpoint="/api/external/ratings",
        )
        if retry_result.success:
            return cast(PMDBSubmitResult, retry_result)

    # If the target score already exists remotely, avoid retry loops.
    for entry in entries:
        if client._rating_entry_matches_score(entry, score):
            return PMDBSubmitResult(
                success=True,
                retryable=False,
                retry_after_seconds=0,
                duplicate_or_exists=True,
                error_text="",
                item_id=client._extract_entry_id(entry),
                status_code=200,
                error_code="exists",
                endpoint="/api/external/ratings",
            )

    if candidate_ids and not deleted_any:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=(
                "PMDB has existing rating entry for this label but it could not be "
                "deleted with the current API key."
            ),
            item_id=None,
            status_code=409,
            error_code="conflict_not_owned",
            endpoint="/api/external/ratings",
        )

    return PMDBSubmitResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        duplicate_or_exists=False,
        error_text="PMDB rating create failed and no replace target was found.",
        item_id=None,
        status_code=500,
        error_code="create_failed_unresolved",
        endpoint="/api/external/ratings",
    )


async def submit_rating(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    score: float,
    existing_pmdb_item_id: Optional[str] = None,
) -> PMDBSubmitResult:
    stale_cached_item_id = False
    if existing_pmdb_item_id:
        delete_result = await client._delete_rating_by_id(existing_pmdb_item_id)
        if not delete_result.success:
            if not _is_not_owned_delete_failure(delete_result):
                return PMDBSubmitResult(
                    success=False,
                    retryable=delete_result.retryable,
                    retry_after_seconds=delete_result.retry_after_seconds
                    if delete_result.retryable
                    else 0,
                    duplicate_or_exists=False,
                    error_text=delete_result.error_text,
                    item_id=None,
                    status_code=delete_result.status_code,
                    error_code=delete_result.error_code,
                    endpoint=delete_result.endpoint or "/api/external/ratings",
                )
            # PMDB confirmed the cached id belongs to another account; the
            # caller should stop treating it as ours regardless of how this
            # submission ultimately resolves.
            stale_cached_item_id = True

    response = await client._post_with_gates(
        url=f"{client.base_url}/api/external/ratings",
        payload={
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "score": score,
            "label": label,
        },
        contribution_gate=client.rating_gate,
    )
    result = client._to_submit_result(
        response,
        endpoint="/api/external/ratings",
    )
    if not result.success and (
        client._is_create_failed_rating(result) or client._is_duplicate_or_exists_result(result)
    ):
        resolved = await client._replace_rating_after_duplicate(
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            score=score,
            known_item_id=existing_pmdb_item_id,
        )
        return cast(PMDBSubmitResult, resolved)
    if stale_cached_item_id:
        result = replace(result, stale_cached_item_id=True)
    return cast(PMDBSubmitResult, result)
