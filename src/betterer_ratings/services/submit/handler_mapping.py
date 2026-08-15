from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, Set

from betterer_ratings.core.clock import format_duration


async def submit_mapping_group(
    *,
    rows: Sequence[Any],
    pmdb_client: Any,
    db: Any,
    submit_mapping_fn: Callable[..., Any],
    now_epoch_fn: Callable[[], int],
    logger: Any,
) -> None:
    """Resolve all existing mappings for a title with one PMDB lookup."""
    if not rows:
        return

    first = rows[0]
    tmdb_id = int(first["tmdb_id"])
    media_type = str(first["media_type"])
    lookup = await pmdb_client._fetch_existing_mappings(tmdb_id, media_type)

    unresolved = list(rows)
    resolved_count = 0
    verified_cached_count = 0
    lookup_valid = (
        lookup.status == 200
        and isinstance(lookup.data, dict)
        and isinstance(lookup.data.get("mappings"), dict)
    )
    untrusted_cached_types: set[str] = set()
    if lookup_valid:
        submitted_at = now_epoch_fn()
        for row in list(unresolved):
            id_type = str(row["id_type"])
            id_value = str(row["id_value"])
            cached_item_id = str(row["pmdb_item_id"] or "").strip() or None
            entries = pmdb_client._extract_mappings_for_type(lookup.data, id_type)
            if cached_item_id:
                cached_entry = next(
                    (
                        entry
                        for entry in entries
                        if pmdb_client._extract_entry_id(entry) == cached_item_id
                    ),
                    None,
                )
                if cached_entry is None:
                    db.clear_mapping_pmdb_item_id(tmdb_id, media_type, id_type)
                    untrusted_cached_types.add(id_type)
                    continue
                matching_entry = (
                    cached_entry
                    if pmdb_client._mapping_entry_matches_value(cached_entry, id_value)
                    else None
                )
            else:
                matching_entry = next(
                    (
                        entry
                        for entry in entries
                        if pmdb_client._mapping_entry_matches_value(entry, id_value)
                    ),
                    None,
                )
            if matching_entry is None:
                continue
            db.mark_mapping_submitted(
                tmdb_id,
                media_type,
                id_type,
                submitted_at,
                pmdb_item_id=cached_item_id,
            )
            unresolved.remove(row)
            resolved_count += 1
            if cached_item_id:
                verified_cached_count += 1

    if resolved_count:
        logger.info(
            "[Submitter] Mapping preflight resolved existing: %s %s resolved=%s missing=%s",
            media_type,
            tmdb_id,
            resolved_count,
            len(unresolved),
            extra={
                "event": "mapping.preflight_resolved",
                "entity": "mapping",
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "resolved": resolved_count,
                "missing": len(unresolved),
            },
        )

    if verified_cached_count:
        logger.info(
            "[Submitter] Mapping cache verified: %s %s verified=%s",
            media_type,
            tmdb_id,
            verified_cached_count,
            extra={
                "event": "mapping.cache_verified",
                "entity": "mapping",
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "verified": verified_cached_count,
            },
        )

    replacement_count = sum(
        1 for row in unresolved if str(row["pmdb_item_id"] or "").strip()
    )
    if replacement_count:
        logger.info(
            "[Submitter] Mapping cache replacement required: %s %s items=%s",
            media_type,
            tmdb_id,
            replacement_count,
            extra={
                "event": "mapping.cache_replacement_required",
                "entity": "mapping",
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "items": replacement_count,
            },
        )

    for row in unresolved:
        cached_item_id = str(row["pmdb_item_id"] or "").strip() or None
        id_type = str(row["id_type"])
        row_to_submit = row
        if cached_item_id and (not lookup_valid or id_type in untrusted_cached_types):
            row_to_submit = dict(row)
            row_to_submit["pmdb_item_id"] = None
        await submit_mapping_fn(row=row_to_submit)


async def submit_mapping(
    *,
    row: Any,
    pmdb_client: Any,
    db: Any,
    verify_after_transient_statuses: Set[int],
    max_retry_attempts: int,
    format_manual_error_fn: Callable[..., str],
    format_pmdb_error_fn: Callable[..., str],
    retry_delay_seconds_fn: Callable[[Any, int], int],
    now_epoch_fn: Callable[[], int],
    first_non_empty_fn: Callable[..., Optional[str]],
    logger: Any,
) -> None:
    tmdb_id = int(row["tmdb_id"])
    media_type = str(row["media_type"])
    id_type = str(row["id_type"])
    id_value = str(row["id_value"])
    pmdb_item_id = first_non_empty_fn(row["pmdb_item_id"])
    attempts = int(row["pmdb_attempts"] or 0)

    result = await pmdb_client.submit_mapping(
        tmdb_id=tmdb_id,
        media_type=media_type,
        id_type=id_type,
        id_value=id_value,
        existing_pmdb_item_id=pmdb_item_id,
    )

    if result.stale_cached_item_id and pmdb_item_id:
        db.clear_mapping_pmdb_item_id(tmdb_id, media_type, id_type)
        logger.info(
            "[Submitter] Mapping cache invalidated before replacement: %s %s %s",
            media_type,
            tmdb_id,
            id_type,
            extra={
                "event": "mapping.cache_invalidated",
                "entity": "mapping",
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "id_type": id_type,
            },
        )
        pmdb_item_id = None

    if result.success:
        db.mark_mapping_submitted(
            tmdb_id,
            media_type,
            id_type,
            now_epoch_fn(),
            pmdb_item_id=result.item_id,
        )
        if result.duplicate_or_exists:
            logger.info(
                "[Submitter] Mapping already exists: %s %s %s=%s",
                media_type,
                tmdb_id,
                id_type,
                id_value,
                extra={
                    "event": "mapping.exists",
                    "entity": "mapping",
                    "media_type": media_type,
                    "tmdb_id": tmdb_id,
                    "id_type": id_type,
                },
            )
        else:
            logger.info(
                "[Submitter] Mapping submitted: %s %s %s=%s",
                media_type,
                tmdb_id,
                id_type,
                id_value,
                extra={
                    "event": "mapping.submitted",
                    "entity": "mapping",
                    "media_type": media_type,
                    "tmdb_id": tmdb_id,
                    "id_type": id_type,
                },
            )
        return

    if result.retryable:
        if int(result.status_code or 0) in verify_after_transient_statuses:
            # `confirm_mapping_exists` matches by id_type+id_value against an
            # unauthenticated lookup, so a hit is not proof the entry is
            # ours — only cache ids that came back from our own create
            # response, never from this confirmation match.
            found_existing, _found_item_id = await pmdb_client.confirm_mapping_exists(
                tmdb_id=tmdb_id,
                media_type=media_type,
                id_type=id_type,
                id_value=id_value,
            )
            if found_existing:
                db.mark_mapping_submitted(
                    tmdb_id,
                    media_type,
                    id_type,
                    now_epoch_fn(),
                    pmdb_item_id=pmdb_item_id,
                )
                logger.info(
                    "[Submitter] Mapping confirmed remote after transient failure: %s %s %s=%s",
                    media_type,
                    tmdb_id,
                    id_type,
                    id_value,
                )
                return

        if attempts + 1 >= max_retry_attempts:
            db.mark_mapping_failed(
                tmdb_id,
                media_type,
                id_type,
                format_manual_error_fn(
                    endpoint=result.endpoint or "/api/external/mappings",
                    status=int(result.status_code or 0),
                    code="max_retry_attempts_exceeded",
                    retryable=False,
                    message=(
                        f"Exceeded max retry attempts ({max_retry_attempts}) "
                        f"for mapping submission."
                    ),
                ),
            )
            logger.error(
                "[Submitter] Mapping failed permanently after max attempts (%s): %s %s %s=%s",
                max_retry_attempts,
                media_type,
                tmdb_id,
                id_type,
                id_value,
            )
            return

        retry_delay = retry_delay_seconds_fn(result, attempts)
        retry_at = now_epoch_fn() + retry_delay
        error_payload = format_pmdb_error_fn(
            endpoint_hint="/api/external/mappings",
            result=result,
        )
        db.mark_mapping_retry(
            tmdb_id,
            media_type,
            id_type,
            retry_at,
            error_payload,
        )
        logger.warning(
            "[Submitter] Mapping retry scheduled in %s: %s %s %s=%s",
            format_duration(retry_delay),
            media_type,
            tmdb_id,
            id_type,
            id_value,
        )
        return

    db.mark_mapping_failed(
        tmdb_id,
        media_type,
        id_type,
        format_pmdb_error_fn(
            endpoint_hint="/api/external/mappings",
            result=result,
        ),
    )
    logger.error(
        "[Submitter] Mapping failed permanently: %s %s %s=%s (%s)",
        media_type,
        tmdb_id,
        id_type,
        id_value,
        result.error_text[:200],
        extra={
            "event": (
                "mapping.foreign_owner_conflict"
                if result.error_code == "mapping_owned_by_other"
                else "mapping.failed"
            ),
            "entity": "mapping",
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "id_type": id_type,
            "error_code": result.error_code,
        },
    )
