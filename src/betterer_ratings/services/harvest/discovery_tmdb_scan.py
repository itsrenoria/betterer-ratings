from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


def _log_scan_progress(
    *,
    logger: Any,
    pages_scanned: int,
    effective_target_pages: int,
    selected_count: int,
    raw_seen_total: int,
    source_name: str,
    page: int,
    pages_effective: int,
) -> None:
    if pages_scanned != 1 and pages_scanned % 25 != 0:
        return
    if logger is None:
        return
    eligible_rate = (selected_count / raw_seen_total) * 100.0 if raw_seen_total > 0 else 0.0
    logger.debug(
        "[Harvester] TMDB scan progress: pages=%s/%s selected=%s raw_seen=%s eligible_rate=%.2f%% current_source=%s page=%s/%s",
        pages_scanned,
        max(pages_scanned, effective_target_pages),
        selected_count,
        raw_seen_total,
        eligible_rate,
        source_name,
        page,
        pages_effective,
    )


def _log_source_summary(*, logger: Any, source: Any, stat: Dict[str, int]) -> None:
    if logger is None:
        return
    logger.info(
        "[TMDB] Source scan complete: source=%s endpoint=%s pages=%s/%s raw_seen=%s added=%s skipped_existing=%s duplicates=%s unsupported=%s errors=%s",
        source.name,
        source.endpoint,
        stat["pages_fetched"],
        stat["pages_effective"],
        stat["raw_seen"],
        stat["added"],
        stat["skipped"],
        stat["duplicates"],
        stat["unsupported"],
        stat["errors"],
    )


def _is_valid_first_page(response: Any) -> bool:
    return (
        bool(response.ok)
        and isinstance(response.data, dict)
        and isinstance(response.data.get("results"), list)
    )


def _is_provider_wide_failure(status: int) -> bool:
    return status in {0, 401, 403, 429} or 500 <= status <= 599


def _process_source_page(
    *,
    response: Any,
    page: int,
    source: Any,
    stat: Dict[str, int],
    parse_int_fn: Callable[[Any], Optional[int]],
    seen: Set[Tuple[str, int]],
    candidates: List[Any],
    candidate_cls: Any,
    publish_scan_progress_fn: Callable[..., None],
    logger: Any,
    pages_scanned: int,
    effective_target_pages: int,
    raw_seen_total: int,
    effective_max_pages: int,
    existing_title_keys: Set[Tuple[str, int]],
) -> Tuple[int, int, int, int]:
    pages_scanned += 1
    if not response.ok or not isinstance(response.data, dict):
        stat["errors"] += 1
        if logger is not None:
            logger.warning(
                "[TMDB] %s page %s failed: %s %s",
                source.name,
                page,
                response.status,
                response.text[:240],
            )
        _log_scan_progress(
            logger=logger,
            pages_scanned=pages_scanned,
            effective_target_pages=effective_target_pages,
            selected_count=len(candidates),
            raw_seen_total=raw_seen_total,
            source_name=source.name,
            page=page,
            pages_effective=stat["pages_effective"],
        )
        publish_scan_progress_fn(source, current_page=page)
        return pages_scanned, effective_target_pages, raw_seen_total, effective_max_pages

    total_page_value = parse_int_fn(response.data.get("total_pages")) or 1
    effective_max_pages = max(1, min(total_page_value, 500, source.max_pages))
    discovered_pages = max(1, min(total_page_value, 500))
    previous_effective = int(stat["pages_effective"])
    stat["pages_discovered"] = discovered_pages
    stat["pages_effective"] = effective_max_pages
    if stat["pages_effective"] != previous_effective:
        effective_target_pages += int(stat["pages_effective"]) - previous_effective

    results = response.data.get("results")
    if not isinstance(results, list):
        _log_scan_progress(
            logger=logger,
            pages_scanned=pages_scanned,
            effective_target_pages=effective_target_pages,
            selected_count=len(candidates),
            raw_seen_total=raw_seen_total,
            source_name=source.name,
            page=page,
            pages_effective=stat["pages_effective"],
        )
        publish_scan_progress_fn(source, current_page=page)
        return pages_scanned, effective_target_pages, raw_seen_total, effective_max_pages

    stat["pages_fetched"] += 1
    stat["raw_seen"] += len(results)
    raw_seen_total += len(results)
    if stat["first_page"] == 0:
        stat["first_page"] = page
    stat["last_page"] = page

    parsed_items: list[tuple[int, str, str, float]] = []

    for item in results:
        if not isinstance(item, dict):
            continue

        tmdb_id = parse_int_fn(item.get("id"))
        if tmdb_id is None:
            continue

        media_type = source.media_type_hint
        if media_type is None:
            item_media = str(item.get("media_type") or "").strip().lower()
            if item_media not in {"movie", "tv"}:
                stat["unsupported"] += 1
                continue
            media_type = item_media

        key = (media_type, tmdb_id)
        if key in seen:
            stat["duplicates"] += 1
            continue
        seen.add(key)
        if key in existing_title_keys:
            stat["skipped"] += 1
            continue

        title = (
            str(item.get("title") or "").strip()
            if media_type == "movie"
            else str(item.get("name") or "").strip()
        )
        popularity = 0.0
        try:
            popularity = float(item.get("popularity") or 0.0)
        except (TypeError, ValueError):
            popularity = 0.0

        parsed_items.append((tmdb_id, media_type, title, popularity))

    for tmdb_id, media_type, title, popularity in parsed_items:
        candidates.append(
            candidate_cls(
                tmdb_id=tmdb_id,
                media_type=media_type,
                title=title or f"TMDB-{tmdb_id}",
                popularity=popularity,
                harvest_reason="source",
            )
        )
        existing_title_keys.add((media_type, tmdb_id))
        stat["added"] += 1
    _log_scan_progress(
        logger=logger,
        pages_scanned=pages_scanned,
        effective_target_pages=effective_target_pages,
        selected_count=len(candidates),
        raw_seen_total=raw_seen_total,
        source_name=source.name,
        page=page,
        pages_effective=stat["pages_effective"],
    )
    publish_scan_progress_fn(source, current_page=page)
    return pages_scanned, effective_target_pages, raw_seen_total, effective_max_pages


async def scan_tmdb_sources(
    *,
    stop_event: Any,
    db: Any,
    tmdb_client: Any,
    tmdb_sources: Any,
    source_stats: Dict[str, Dict[str, int]],
    candidates: List[Any],
    seen: Set[Tuple[str, int]],
    parse_int_fn: Callable[[Any], Optional[int]],
    candidate_cls: Any,
    publish_scan_progress_fn: Callable[..., None],
    logger: Any,
    page_batch_concurrency: int,
    pages_scanned: int,
    effective_target_pages: int,
    raw_seen_total: int,
) -> Tuple[bool, int, int, int]:
    interrupted = False
    effective_batch_concurrency = max(1, int(page_batch_concurrency))
    existing_title_keys: Set[Tuple[str, int]] = (
        db.title_key_set() if hasattr(db, "title_key_set") else set()
    )

    halt_tmdb_sources = False
    for source_index, source in enumerate(tmdb_sources):
        if stop_event.is_set():
            interrupted = True
            break
        stat = source_stats[source.name]
        effective_max_pages = source.max_pages
        if logger is not None:
            logger.info(
                "[TMDB] Source scan started: source=%s endpoint=%s max_pages=%s",
                source.name,
                source.endpoint,
                source.max_pages,
            )

        page = 1
        while page <= source.max_pages:
            if stop_event.is_set():
                interrupted = True
                break
            if page > effective_max_pages:
                break

            if page == 1:
                try:
                    first_response = await tmdb_client.fetch_source_page(source, 1)
                except Exception as exc:
                    first_response = None
                    if logger is not None:
                        logger.warning(
                            "[TMDB] %s page 1 failed with exception: %s",
                            source.name,
                            exc,
                        )
                if first_response is None or not _is_valid_first_page(first_response):
                    status = int(getattr(first_response, "status", 0) or 0)
                    pages_scanned += 1
                    stat["errors"] += 1
                    stat["aborted"] = 1
                    stat["abort_status"] = status
                    previous_effective = int(stat["pages_effective"])
                    stat["pages_effective"] = 1
                    effective_target_pages += 1 - previous_effective
                    publish_scan_progress_fn(source, current_page=1)
                    provider_wide = _is_provider_wide_failure(status)
                    if logger is not None:
                        logger.warning(
                            "[TMDB] Source aborted after invalid first page: source=%s status=%s provider_wide=%s",
                            source.name,
                            status,
                            provider_wide,
                            extra={
                                "event": "tmdb.source_aborted",
                                "source": source.name,
                                "status": status,
                                "provider_wide": provider_wide,
                            },
                        )
                    if provider_wide:
                        for remaining_source in tmdb_sources[source_index + 1 :]:
                            remaining_stat = source_stats[remaining_source.name]
                            effective_target_pages -= int(
                                remaining_stat["pages_effective"]
                            )
                            remaining_stat["pages_effective"] = 0
                            remaining_stat["aborted"] = 1
                            remaining_stat["abort_status"] = status
                        halt_tmdb_sources = True
                    break
                (
                    pages_scanned,
                    effective_target_pages,
                    raw_seen_total,
                    effective_max_pages,
                ) = _process_source_page(
                    response=first_response,
                    page=1,
                    source=source,
                    stat=stat,
                    parse_int_fn=parse_int_fn,
                    seen=seen,
                    candidates=candidates,
                    candidate_cls=candidate_cls,
                    publish_scan_progress_fn=publish_scan_progress_fn,
                    logger=logger,
                    pages_scanned=pages_scanned,
                    effective_target_pages=effective_target_pages,
                    raw_seen_total=raw_seen_total,
                    effective_max_pages=effective_max_pages,
                    existing_title_keys=existing_title_keys,
                )
                page = 2
                continue

            batch_end = min(effective_max_pages, page + effective_batch_concurrency - 1)
            pages_batch = list(range(page, batch_end + 1))
            responses = await asyncio.gather(
                *(tmdb_client.fetch_source_page(source, pageno) for pageno in pages_batch),
                return_exceptions=True,
            )
            for pageno, response in zip(pages_batch, responses):  # noqa: B905
                if stop_event.is_set():
                    interrupted = True
                    break

                if isinstance(response, Exception):
                    pages_scanned += 1
                    stat["errors"] += 1
                    if logger is not None:
                        logger.warning(
                            "[TMDB] %s page %s failed with exception: %s",
                            source.name,
                            pageno,
                            response,
                        )
                    _log_scan_progress(
                        logger=logger,
                        pages_scanned=pages_scanned,
                        effective_target_pages=effective_target_pages,
                        selected_count=len(candidates),
                        raw_seen_total=raw_seen_total,
                        source_name=source.name,
                        page=pageno,
                        pages_effective=stat["pages_effective"],
                    )
                    publish_scan_progress_fn(source, current_page=pageno)
                    continue
                (
                    pages_scanned,
                    effective_target_pages,
                    raw_seen_total,
                    effective_max_pages,
                ) = _process_source_page(
                    response=response,
                    page=pageno,
                    source=source,
                    stat=stat,
                    parse_int_fn=parse_int_fn,
                    seen=seen,
                    candidates=candidates,
                    candidate_cls=candidate_cls,
                    publish_scan_progress_fn=publish_scan_progress_fn,
                    logger=logger,
                    pages_scanned=pages_scanned,
                    effective_target_pages=effective_target_pages,
                    raw_seen_total=raw_seen_total,
                    effective_max_pages=effective_max_pages,
                    existing_title_keys=existing_title_keys,
                )

            page = batch_end + 1
            if interrupted:
                break

        _log_source_summary(logger=logger, source=source, stat=stat)
        if interrupted or halt_tmdb_sources:
            break

    return interrupted, pages_scanned, effective_target_pages, raw_seen_total
