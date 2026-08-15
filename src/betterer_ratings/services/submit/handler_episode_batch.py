from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence


async def submit_episode_ratings_batch(
    *,
    rows: Sequence[Any],
    pmdb_client: Any,
    db: Any,
    max_retry_attempts: int,
    format_manual_error_fn: Callable[..., str],
    retry_delay_seconds_fn: Callable[[Any, int], int],
    now_epoch_fn: Callable[[], int],
    first_non_empty_fn: Callable[..., Optional[str]],
    parse_int_fn: Callable[[Any], Optional[int]],
    parse_retry_after_fn: Callable[[Optional[str], int], int],
    pmdb_submit_result_cls: Any,
    extract_error_code_fn: Callable[[Any, str], str],
    is_cloudflare_challenge_fn: Callable[[Any], bool],
    logger: Any,
) -> None:
    if not rows:
        return

    first = rows[0]
    tmdb_id = int(first["tmdb_id"])
    media_type = str(first["media_type"])
    season = int(first["season"])
    label = str(first["label"])
    endpoint = "/api/external/episode-ratings/batch"
    rows_to_create: list[Any] = [
        row for row in rows if not first_non_empty_fn(row["pmdb_item_id"])
    ]
    cached_rows = [row for row in rows if first_non_empty_fn(row["pmdb_item_id"])]

    def clear_cached_id(row: Any) -> None:
        db.clear_episode_rating_pmdb_item_id(
            tmdb_id,
            media_type,
            season,
            int(row["episode"]),
            str(row["label"]),
        )

    def mark_retryable_delete(
        row: Any,
        *,
        status: int,
        code: str,
        message: str,
        retry_after_seconds: int,
        failure_endpoint: str,
    ) -> None:
        episode = int(row["episode"])
        attempts = int(row["pmdb_attempts"] or 0)
        pseudo_result = pmdb_submit_result_cls(
            success=False,
            retryable=True,
            retry_after_seconds=max(5, retry_after_seconds),
            duplicate_or_exists=False,
            error_text=message,
            item_id=None,
            status_code=status,
            error_code=code,
            endpoint=failure_endpoint,
        )
        if attempts + 1 >= max_retry_attempts:
            db.mark_episode_rating_failed(
                tmdb_id,
                media_type,
                season,
                episode,
                str(row["label"]),
                format_manual_error_fn(
                    endpoint=failure_endpoint,
                    status=status,
                    code="max_retry_attempts_exceeded",
                    retryable=False,
                    message=(
                        f"Exceeded max retry attempts ({max_retry_attempts}) "
                        "for episode rating delete+create."
                    ),
                ),
            )
            return
        retry_delay = retry_delay_seconds_fn(pseudo_result, attempts)
        db.mark_episode_rating_retry(
            tmdb_id,
            media_type,
            season,
            episode,
            str(row["label"]),
            now_epoch_fn() + retry_delay,
            format_manual_error_fn(
                endpoint=failure_endpoint,
                status=status,
                code=code or "delete_failed",
                retryable=True,
                message=message,
            ),
        )

    if cached_rows:
        cached_ids = [
            str(first_non_empty_fn(row["pmdb_item_id"])) for row in cached_rows
        ]
        batch_delete_response = await pmdb_client.delete_episode_ratings_batch(cached_ids)
        batch_status = int(batch_delete_response.status or 0)
        cloudflare_challenge = is_cloudflare_challenge_fn(batch_delete_response)
        transient_batch_failure = (
            batch_status in {0, 401, 429}
            or 500 <= batch_status <= 599
            or cloudflare_challenge
        )
        batch_payload = (
            batch_delete_response.data
            if isinstance(batch_delete_response.data, dict)
            else {}
        )
        deleted_ids_value = batch_payload.get("deletedIds")
        deleted_ids = (
            {
                str(item_id)
                for item_id in deleted_ids_value
                if first_non_empty_fn(item_id)
            }
            if batch_status in {200, 207} and isinstance(deleted_ids_value, list)
            else set()
        )
        logger.info(
            "[Submitter] Episode batch delete: requested=%s deleted=%s unresolved=%s status=%s",
            len(cached_ids),
            len(deleted_ids),
            len(cached_ids) - len(deleted_ids),
            batch_status,
            extra={
                "event": "episode_ratings.batch_delete",
                "requested": len(cached_ids),
                "deleted": len(deleted_ids),
                "unresolved": len(cached_ids) - len(deleted_ids),
                "status": batch_status,
            },
        )
        if transient_batch_failure:
            retry_after = (
                parse_retry_after_fn(
                    batch_delete_response.headers.get("retry-after"), 30
                )
                if batch_status == 429
                else (300 if batch_status == 401 or cloudflare_challenge else 30)
            )
            code = extract_error_code_fn(
                batch_delete_response.data, batch_delete_response.text or ""
            )
            for row in rows:
                mark_retryable_delete(
                    row,
                    status=batch_status,
                    code=code or "batch_delete_failed",
                    message=batch_delete_response.text or "Episode batch delete deferred",
                    retry_after_seconds=retry_after,
                    failure_endpoint=endpoint,
                )
            return

        unresolved_cached_rows: list[Any] = []
        for row in cached_rows:
            item_id = str(first_non_empty_fn(row["pmdb_item_id"]))
            if item_id in deleted_ids:
                clear_cached_id(row)
                rows_to_create.append(row)
            else:
                unresolved_cached_rows.append(row)

        for row in unresolved_cached_rows:
            episode = int(row["episode"])
            item_id = str(first_non_empty_fn(row["pmdb_item_id"]))
            delete_result = await pmdb_client.delete_episode_rating_by_id(item_id)
            if delete_result.success:
                clear_cached_id(row)
                rows_to_create.append(row)
                continue

            message = delete_result.error_text or "Failed to delete existing episode rating"
            not_owned = (
                int(delete_result.status_code or 0) == 403
                and "only delete your own data"
                in f"{delete_result.error_code} {message}".lower()
            )
            if not_owned:
                clear_cached_id(row)
                rows_to_create.append(row)
                continue
            if delete_result.retryable:
                mark_retryable_delete(
                    row,
                    status=int(delete_result.status_code or 0),
                    code=delete_result.error_code or "delete_failed",
                    message=message,
                    retry_after_seconds=int(delete_result.retry_after_seconds or 30),
                    failure_endpoint=delete_result.endpoint or endpoint,
                )
                continue

            db.mark_episode_rating_failed(
                tmdb_id,
                media_type,
                season,
                episode,
                label,
                format_manual_error_fn(
                    endpoint=delete_result.endpoint or endpoint,
                    status=int(delete_result.status_code or 0),
                    code=delete_result.error_code or "delete_failed",
                    retryable=False,
                    message=message,
                ),
            )

    if not rows_to_create:
        return

    payload_ratings: list[Dict[str, Any]] = []
    for row in rows_to_create:
        payload_ratings.append(
            {
                "episode": int(row["episode"]),
                "score": float(row["score"]),
                "label": str(row["label"]),
            }
        )

    response = await pmdb_client.submit_episode_ratings_batch(
        tmdb_id=tmdb_id,
        media_type=media_type,
        season=season,
        label=label,
        ratings=payload_ratings,
    )

    payload = response.data if isinstance(response.data, dict) else {}
    created_items: Dict[int, Optional[str]] = {}
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_episode = parse_int_fn(item.get("episode"))
            if item_episode is None:
                continue
            created_items[int(item_episode)] = first_non_empty_fn(item.get("id"))

    unresolved_rows: list[Any] = []
    submitted_at = now_epoch_fn()
    for row in rows_to_create:
        episode = int(row["episode"])
        created_item_id = created_items.get(episode)
        if episode in created_items:
            db.mark_episode_rating_submitted(
                tmdb_id,
                media_type,
                season,
                episode,
                str(row["label"]),
                submitted_at,
                pmdb_item_id=created_item_id,
            )
        else:
            unresolved_rows.append(row)

    for row in list(unresolved_rows):
        episode = int(row["episode"])
        score = float(row["score"])
        found_existing, found_item_id = await pmdb_client.confirm_episode_rating_exists(
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=str(row["label"]),
            score=score,
        )
        if not found_existing:
            continue
        db.mark_episode_rating_submitted(
            tmdb_id,
            media_type,
            season,
            episode,
            str(row["label"]),
            submitted_at,
            pmdb_item_id=found_item_id,
        )
        unresolved_rows.remove(row)

    if not unresolved_rows:
        logger.info(
            "[Submitter] Episode batch submitted: %s %s season=%s items=%s",
            media_type,
            tmdb_id,
            season,
            len(rows_to_create),
            extra={
                "event": "episode_ratings.submitted",
                "entity": "episode_ratings",
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "season": season,
                "items": len(rows_to_create),
            },
        )
        return

    status_code = int(response.status or 0)
    cloudflare_challenge = is_cloudflare_challenge_fn(response)
    retryable = status_code in {0, 401, 429, 500, 502, 503, 504, 207} or cloudflare_challenge
    error_code = extract_error_code_fn(response.data, response.text or "")
    base_retry_after = (
        parse_retry_after_fn(response.headers.get("retry-after"), 30)
        if status_code == 429
        else (300 if cloudflare_challenge else 30)
    )

    for row in unresolved_rows:
        episode = int(row["episode"])
        attempts = int(row["pmdb_attempts"] or 0)
        score = float(row["score"])
        label_value = str(row["label"])
        message = response.text or f"Episode batch submit unresolved for episode {episode}"
        if retryable:
            pseudo_result = pmdb_submit_result_cls(
                success=False,
                retryable=True,
                retry_after_seconds=base_retry_after,
                duplicate_or_exists=False,
                error_text=message,
                item_id=None,
                status_code=status_code,
                error_code=error_code,
                endpoint=endpoint,
            )
            if attempts + 1 >= max_retry_attempts:
                db.mark_episode_rating_failed(
                    tmdb_id,
                    media_type,
                    season,
                    episode,
                    label_value,
                    format_manual_error_fn(
                        endpoint=endpoint,
                        status=status_code,
                        code="max_retry_attempts_exceeded",
                        retryable=False,
                        message=(
                            f"Exceeded max retry attempts ({max_retry_attempts}) "
                            "for episode batch submission."
                        ),
                    ),
                )
            else:
                retry_delay = retry_delay_seconds_fn(pseudo_result, attempts)
                retry_at = now_epoch_fn() + retry_delay
                db.mark_episode_rating_retry(
                    tmdb_id,
                    media_type,
                    season,
                    episode,
                    label_value,
                    retry_at,
                    format_manual_error_fn(
                        endpoint=endpoint,
                        status=status_code,
                        code=error_code or "batch_unresolved",
                        retryable=True,
                        message=(f"{message[:220]} episode={episode} score={score:.1f}"),
                    ),
                )
            continue

        db.mark_episode_rating_failed(
            tmdb_id,
            media_type,
            season,
            episode,
            label_value,
            format_manual_error_fn(
                endpoint=endpoint,
                status=status_code,
                code=error_code or "batch_failed",
                retryable=False,
                message=f"{message[:220]} episode={episode} score={score:.1f}",
            ),
        )
    logger.warning(
        "[Submitter] Episode batch unresolved: %s %s season=%s unresolved=%s status=%s",
        media_type,
        tmdb_id,
        season,
        len(unresolved_rows),
        status_code,
    )
