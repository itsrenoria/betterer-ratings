from __future__ import annotations

from typing import Any, cast

from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.domain.models import PMDBDeleteResult, PMDBSubmitResult


async def delete_mapping_by_id(client: Any, mapping_id: str) -> PMDBDeleteResult:
    response = await client._delete_with_gates(
        url=f"{client.base_url}/api/external/mappings/{mapping_id}",
        contribution_gate=client.mapping_gate,
    )
    return cast(PMDBDeleteResult, client._to_delete_result(
        response,
        endpoint=f"/api/external/mappings/{mapping_id}",
    ))


async def resolve_mapping_duplicate_or_conflict(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    id_value: str,
) -> PMDBSubmitResult:
    # The title-scoped GET /api/external/mappings collection is not an
    # authoritative view of PMDB's create-uniqueness check: it can omit or
    # misreport entries that PMDB itself considers a duplicate. The reverse
    # lookup by id_type+id_value reflects true ownership instead.
    lookup = await client._fetch_mapping_owners(
        id_type=id_type,
        id_value=id_value,
        media_type=media_type,
    )
    if lookup.status in (429, 500, 502, 503, 504, 0):
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=parse_retry_after(lookup.headers.get("retry-after"), 30),
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB mapping lookup failed",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/mappings/lookup",
        )
    if lookup.status == 401:
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=300,
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB unauthorized while looking up mapping owners",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/mappings/lookup",
        )
    if lookup.status == 403:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB forbidden while looking up mapping owners",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/mappings/lookup",
        )

    if lookup.status != 200:
        # Any other lookup failure (400/404/422/etc.) is a lookup-level
        # error, not evidence one way or the other about ownership -- it
        # must not be silently converted into duplicate_unresolved.
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=lookup.text or "PMDB mapping ownership lookup failed",
            item_id=None,
            status_code=lookup.status,
            error_code=client._extract_error_code(lookup.data, lookup.text),
            endpoint="/api/external/mappings/lookup",
        )

    if client._mapping_lookup_owned_by(lookup.data, tmdb_id, media_type):
        return PMDBSubmitResult(
            success=True,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=True,
            error_text="",
            item_id=None,
            status_code=200,
            error_code="exists",
            endpoint="/api/external/mappings/lookup",
        )

    total_owners = lookup.data.get("total") if isinstance(lookup.data, dict) else None
    if isinstance(total_owners, int) and total_owners > 0:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=(
                "PMDB reported duplicate/conflict for mapping create, but the "
                f"{id_type}={id_value} mapping is owned by a different title."
            ),
            item_id=None,
            status_code=409,
            error_code="mapping_owned_by_other",
            endpoint="/api/external/mappings/lookup",
        )

    return PMDBSubmitResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        duplicate_or_exists=False,
        error_text=(
            "PMDB reported duplicate/conflict for mapping create, but exact mapping "
            "was not found during confirmation."
        ),
        item_id=None,
        status_code=409,
        error_code="duplicate_unresolved",
        endpoint="/api/external/mappings/lookup",
    )


async def submit_mapping(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    id_value: str,
) -> PMDBSubmitResult:
    response = await client._post_with_gates(
        url=f"{client.base_url}/api/external/mappings",
        payload={
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "id_type": id_type,
            "id_value": id_value,
        },
        contribution_gate=client.mapping_gate,
    )
    result = client._to_submit_result(
        response,
        endpoint="/api/external/mappings",
    )
    if not result.success and (
        client._is_create_failed_mapping(result) or client._is_duplicate_or_exists_result(result)
    ):
        resolved = await client._resolve_mapping_duplicate_or_conflict(
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            id_value=id_value,
        )
        return cast(PMDBSubmitResult, resolved)
    return cast(PMDBSubmitResult, result)
