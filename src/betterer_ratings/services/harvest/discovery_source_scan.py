from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from betterer_ratings.core.clock import now_epoch as core_now_epoch
from betterer_ratings.core.parsing import parse_int as core_parse_int
from betterer_ratings.domain.models import Candidate
from betterer_ratings.services.harvest import discovery_imdb_scan as harvest_discovery_imdb_scan
from betterer_ratings.services.harvest import discovery_tmdb_scan as harvest_discovery_tmdb_scan


async def collect_source_candidates(
    *,
    stop_event: asyncio.Event,
    db: Any,
    tmdb_client: Any,
    tmdb_sources: Sequence[Any],
    scan_sources: Sequence[Any],
    imdb_archive_source: Optional[Any],
    imdb_titles_enabled: bool,
    ensure_imdb_index_fn: Callable[[Any], int],
    read_imdb_index_batch_fn: Callable[[Any], Tuple[List[Any], int, int, bool]],
    commit_imdb_cursor_fn: Callable[..., None],
    map_imdb_candidates_to_tmdb_fn: Callable[..., Any],
    imdb_cursor_line_key: str,
    imdb_total_key: str,
    parse_int_fn: Callable[[Any], Optional[int]] = core_parse_int,
    now_epoch_fn: Callable[[], int] = core_now_epoch,
    logger: Any = None,
    candidate_cls: Any = Candidate,
) -> Tuple[List[Candidate], Dict[str, Dict[str, int]], bool]:
    candidates: List[Candidate] = []
    seen: Set[Tuple[str, int]] = set()
    interrupted = False

    now_ts = now_epoch_fn()
    cycle_started_ts = now_ts
    configured_target_pages = sum(source.max_pages for source in tmdb_sources)
    if imdb_archive_source is not None and imdb_titles_enabled:
        configured_target_pages += imdb_archive_source.max_pages
    effective_target_pages = configured_target_pages
    pages_scanned = 0
    raw_seen_total = 0

    source_stats: Dict[str, Dict[str, int]] = {
        source.name: {
            "pages_configured": source.max_pages,
            "pages_effective": source.max_pages,
            "pages_discovered": 0,
            "pages_fetched": 0,
            "raw_seen": 0,
            "added": 0,
            "duplicates": 0,
            "skipped": 0,
            "unsupported": 0,
            "errors": 0,
            "first_page": 0,
            "last_page": 0,
            "aborted": 0,
            "abort_status": 0,
        }
        for source in scan_sources
    }

    def publish_scan_progress(
        current_source: Any,
        *,
        current_page: int,
    ) -> None:
        del current_source, current_page

    (
        tmdb_interrupted,
        pages_scanned,
        effective_target_pages,
        raw_seen_total,
    ) = await harvest_discovery_tmdb_scan.scan_tmdb_sources(
        stop_event=stop_event,
        db=db,
        tmdb_client=tmdb_client,
        tmdb_sources=tmdb_sources,
        source_stats=source_stats,
        candidates=candidates,
        seen=seen,
        parse_int_fn=parse_int_fn,
        candidate_cls=candidate_cls,
        publish_scan_progress_fn=publish_scan_progress,
        logger=logger,
        page_batch_concurrency=max(1, int(getattr(tmdb_client, "source_scan_concurrency", 8))),
        pages_scanned=pages_scanned,
        effective_target_pages=effective_target_pages,
        raw_seen_total=raw_seen_total,
    )
    interrupted = tmdb_interrupted

    if (
        not interrupted
        and imdb_archive_source is not None
        and imdb_titles_enabled
    ):
        (
            imdb_interrupted,
            pages_scanned,
            raw_seen_total,
        ) = await harvest_discovery_imdb_scan.scan_imdb_archive_source(
            stop_event=stop_event,
            db=db,
            imdb_archive_source=imdb_archive_source,
            source_stats=source_stats,
            candidates=candidates,
            seen=seen,
            ensure_imdb_index_fn=ensure_imdb_index_fn,
            read_imdb_index_batch_fn=read_imdb_index_batch_fn,
            commit_imdb_cursor_fn=commit_imdb_cursor_fn,
            map_imdb_candidates_to_tmdb_fn=map_imdb_candidates_to_tmdb_fn,
            imdb_cursor_line_key=imdb_cursor_line_key,
            imdb_total_key=imdb_total_key,
            publish_scan_progress_fn=publish_scan_progress,
            logger=logger,
            pages_scanned=pages_scanned,
            raw_seen_total=raw_seen_total,
        )
        interrupted = imdb_interrupted

    cycle_finished_ts = now_epoch_fn()
    cycle_metrics = {
        "started_at": cycle_started_ts,
        "finished_at": cycle_finished_ts,
        "pages_scanned": pages_scanned,
        "max_pages_target_configured": configured_target_pages,
        "max_pages_target_effective": max(pages_scanned, effective_target_pages),
        "selected_candidates": len(candidates),
        "raw_seen": raw_seen_total,
        "interrupted": interrupted,
        "source_order": [source.name for source in scan_sources],
        "sources": source_stats,
    }
    db.set_state("tmdb_cycle_metrics", json.dumps(cycle_metrics))

    eligible_rate = (len(candidates) / raw_seen_total) * 100.0 if raw_seen_total > 0 else 0.0
    if logger is not None:
        logger.info(
            "[Harvester] Source scan complete: selected=%s pages=%s/%s raw_seen=%s eligible_rate=%.2f%% interrupted=%s.",
            len(candidates),
            pages_scanned,
            max(pages_scanned, effective_target_pages),
            raw_seen_total,
            eligible_rate,
            int(interrupted),
        )
    return candidates, source_stats, interrupted
