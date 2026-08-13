from __future__ import annotations

import asyncio
from typing import Any, Sequence

QUEUE_KINDS = ("mapping", "rating", "episode_ratings")


def queue_order_for_worker(worker_id: int, worker_count: int) -> tuple[str, ...]:
    """Reserve roughly half the workers for mappings and 3/8 for ratings."""
    safe_count = max(1, int(worker_count))
    safe_id = max(1, min(safe_count, int(worker_id)))
    if safe_count == 1:
        return ("mapping", "rating", "episode_ratings")

    mapping_workers = max(1, safe_count // 2)
    rating_workers = max(1, (safe_count * 3) // 8)
    if mapping_workers + rating_workers > safe_count:
        rating_workers = safe_count - mapping_workers

    if safe_id <= mapping_workers:
        return ("mapping", "rating", "episode_ratings")
    if safe_id <= mapping_workers + rating_workers:
        return ("rating", "mapping", "episode_ratings")
    return ("episode_ratings", "rating", "mapping")


async def worker_loop(
    *,
    stop_event: asyncio.Event,
    db: Any,
    poll_seconds: float,
    now_epoch_fn: Any,
    submit_mapping_group_fn: Any,
    submit_rating_fn: Any,
    submit_episode_ratings_batch_fn: Any,
    episode_batch_size: int = 50,
    queue_order: Sequence[str] = QUEUE_KINDS,
) -> None:
    while not stop_event.is_set():
        did_work = False

        now_ts = now_epoch_fn()
        for kind in queue_order:
            if kind == "mapping":
                mapping_rows = db.claim_next_pending_mapping_group(now_ts)
                if mapping_rows:
                    did_work = True
                    await submit_mapping_group_fn(mapping_rows)
                    break
            elif kind == "rating":
                rating_row = db.claim_next_pending_rating(now_ts)
                if rating_row is not None:
                    did_work = True
                    await submit_rating_fn(rating_row)
                    break
            elif kind == "episode_ratings":
                episode_rows = db.claim_next_pending_episode_ratings_batch(
                    now_ts=now_ts,
                    batch_size=episode_batch_size,
                )
                if episode_rows:
                    did_work = True
                    await submit_episode_ratings_batch_fn(episode_rows)
                    break

        if not did_work:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
