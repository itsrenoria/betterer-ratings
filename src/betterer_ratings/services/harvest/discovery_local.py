from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from betterer_ratings.core.clock import now_epoch as core_now_epoch
from betterer_ratings.core.parsing import parse_int as core_parse_int
from betterer_ratings.domain.models import Candidate


async def collect_local_candidates(
    *,
    stop_event: asyncio.Event,
    db: Any,
    day_key: str,
    ratings_ttl_seconds: int,
    failed_retry_seconds: int,
    init_local_stats_fn: Callable[[], Dict[str, int]],
    local_due_counts_fn: Callable[[int], Dict[str, int]],
    parse_int_fn: Callable[[Any], Optional[int]] = core_parse_int,
    now_epoch_fn: Callable[[], int] = core_now_epoch,
    candidate_cls: Any = Candidate,
) -> Tuple[List[Candidate], Dict[str, int], bool]:
    del day_key
    stats = init_local_stats_fn()
    now_ts = now_epoch_fn()
    due_counts = local_due_counts_fn(now_ts)
    stats["due_total"] = due_counts["total"]
    stats["due_failed"] = due_counts["failed"]
    stats["due_new"] = due_counts["new"]
    stats["due_ttl"] = due_counts["ttl"]

    if stop_event.is_set():
        return [], stats, True
    limit = max(0, int(due_counts["total"]))
    if limit <= 0:
        return [], stats, False

    rows = db.select_local_due_titles(
        now_ts=now_ts,
        ratings_ttl_seconds=ratings_ttl_seconds,
        limit=limit,
        failed_retry_seconds=failed_retry_seconds,
    )

    candidates: List[Candidate] = []
    for row in rows:
        tmdb_id = parse_int_fn(row["tmdb_id"])
        media_type = str(row["media_type"] or "").strip().lower()
        if tmdb_id is None or media_type not in {"movie", "tv"}:
            continue
        title = str(row["title"] or "").strip() or f"TMDB-{tmdb_id}"
        try:
            popularity = float(row["popularity"] or 0.0)
        except (TypeError, ValueError):
            popularity = 0.0
        harvest_reason = str(row["harvest_reason"] or "").strip().lower()
        candidates.append(
            candidate_cls(
                tmdb_id=tmdb_id,
                media_type=media_type,
                title=title,
                popularity=popularity,
                harvest_reason=harvest_reason,
            )
        )
        if harvest_reason == "failed":
            stats["selected_failed"] += 1
        elif harvest_reason == "new":
            stats["selected_new"] += 1
        elif harvest_reason == "ttl":
            stats["selected_ttl"] += 1
        else:
            # Defensive fallback; query already filters to known reasons.
            stats["selected_new"] += 1

    stats["selected_total"] = len(candidates)
    return candidates, stats, False


def mdblist_daily_quota_pause_until(
    *,
    db: Any,
    now_ts: int,
    parse_int_fn: Callable[[Any], Optional[int]] = core_parse_int,
) -> int:
    row = db.get_service_state("mdblist")
    if row is None:
        return 0

    paused_until = max(0, int(row["paused_until"] or 0))
    pause_reason = str(row["pause_reason"] or "").strip().lower()
    rate_remaining = parse_int_fn(row["rate_remaining"])
    rate_reset = max(0, int(parse_int_fn(row["rate_reset"]) or 0))

    if rate_remaining == 0 and rate_reset > int(now_ts):
        return rate_reset
    is_quota_pause = (
        pause_reason == "daily limit reached"
        or pause_reason.startswith("daily reserve floor reached")
        or rate_remaining == 0
    )
    if paused_until > int(now_ts) and is_quota_pause:
        return paused_until
    return 0


def source_scan_due(
    *,
    db: Any,
    source_scan_last_run_key: str,
    now_ts: int,
    source_scan_interval_seconds: int,
) -> bool:
    last_run = max(0, db.get_state_int(source_scan_last_run_key, 0))
    if last_run <= 0:
        return True
    return (int(now_ts) - int(last_run)) >= source_scan_interval_seconds
