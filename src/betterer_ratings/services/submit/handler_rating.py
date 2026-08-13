from __future__ import annotations

from typing import Any, Callable, Optional, Set

from betterer_ratings.core.clock import format_duration


async def submit_rating(
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
    label = str(row["label"])
    score = float(row["score"])
    pmdb_item_id = first_non_empty_fn(row["pmdb_item_id"])
    attempts = int(row["pmdb_attempts"] or 0)

    result = await pmdb_client.submit_rating(
        tmdb_id=tmdb_id,
        media_type=media_type,
        label=label,
        score=score,
        existing_pmdb_item_id=pmdb_item_id,
    )

    if result.stale_cached_item_id and pmdb_item_id:
        # PMDB confirmed our cached id belongs to another account; stop
        # trusting it so future cycles don't try to delete it again.
        db.clear_rating_pmdb_item_id(tmdb_id, media_type, label)
        pmdb_item_id = None

    if result.success:
        submitted_item_id = (
            None
            if result.stale_cached_item_id and result.duplicate_or_exists
            else result.item_id
        )
        db.mark_rating_submitted(
            tmdb_id,
            media_type,
            label,
            now_epoch_fn(),
            pmdb_item_id=submitted_item_id,
        )
        if result.duplicate_or_exists:
            logger.info(
                "[Submitter] Rating already exists: %s %s %s=%.1f",
                media_type,
                tmdb_id,
                label,
                score,
                extra={
                    "event": "rating.exists",
                    "entity": "rating",
                    "media_type": media_type,
                    "tmdb_id": tmdb_id,
                    "label": label,
                    "score": score,
                },
            )
        else:
            logger.info(
                "[Submitter] Rating submitted: %s %s %s=%.1f",
                media_type,
                tmdb_id,
                label,
                score,
                extra={
                    "event": "rating.submitted",
                    "entity": "rating",
                    "media_type": media_type,
                    "tmdb_id": tmdb_id,
                    "label": label,
                    "score": score,
                },
            )
        return

    if result.retryable:
        if int(result.status_code or 0) in verify_after_transient_statuses:
            # `confirm_rating_exists` matches by label+score against an
            # unauthenticated lookup, so a hit is not proof the entry is
            # ours to delete later — only cache ids that came back from our
            # own create response, never from this confirmation match.
            found_existing, _found_item_id = await pmdb_client.confirm_rating_exists(
                tmdb_id=tmdb_id,
                media_type=media_type,
                label=label,
                score=score,
            )
            if found_existing:
                db.mark_rating_submitted(
                    tmdb_id,
                    media_type,
                    label,
                    now_epoch_fn(),
                    pmdb_item_id=pmdb_item_id,
                )
                logger.info(
                    "[Submitter] Rating confirmed remote after transient failure: %s %s %s=%.1f",
                    media_type,
                    tmdb_id,
                    label,
                    score,
                )
                return

        if attempts + 1 >= max_retry_attempts:
            db.mark_rating_failed(
                tmdb_id,
                media_type,
                label,
                format_manual_error_fn(
                    endpoint=result.endpoint or "/api/external/ratings",
                    status=int(result.status_code or 0),
                    code="max_retry_attempts_exceeded",
                    retryable=False,
                    message=(
                        f"Exceeded max retry attempts ({max_retry_attempts}) for rating submission."
                    ),
                ),
            )
            logger.error(
                "[Submitter] Rating failed permanently after max attempts (%s): %s %s %s=%.1f",
                max_retry_attempts,
                media_type,
                tmdb_id,
                label,
                score,
            )
            return

        retry_delay = retry_delay_seconds_fn(result, attempts)
        retry_at = now_epoch_fn() + retry_delay
        error_payload = format_pmdb_error_fn(
            endpoint_hint="/api/external/ratings",
            result=result,
        )
        db.mark_rating_retry(
            tmdb_id,
            media_type,
            label,
            retry_at,
            error_payload,
        )
        logger.warning(
            "[Submitter] Rating retry scheduled in %s: %s %s %s=%.1f",
            format_duration(retry_delay),
            media_type,
            tmdb_id,
            label,
            score,
        )
        return

    db.mark_rating_failed(
        tmdb_id,
        media_type,
        label,
        format_pmdb_error_fn(
            endpoint_hint="/api/external/ratings",
            result=result,
        ),
    )
    logger.error(
        "[Submitter] Rating failed permanently: %s %s %s=%.1f (%s)",
        media_type,
        tmdb_id,
        label,
        score,
        result.error_text[:200],
    )
