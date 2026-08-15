from __future__ import annotations

import asyncio
import logging

from betterer_ratings.core.ids import normalize_imdb_title_id
from betterer_ratings.core.parsing import parse_int
from betterer_ratings.domain.models import APIResponse, Candidate, TMDBSource
from betterer_ratings.services.harvest.details import fetch_tmdb_details
from betterer_ratings.services.harvest.discovery_tmdb_scan import scan_tmdb_sources
from betterer_ratings.services.harvest.imdb_mapping_helpers import resolve_imdb_to_tmdb_local


class _FakeDB:
    def title_has_imdb_mapping(self, *, tmdb_id: int, media_type: str) -> bool:
        return tmdb_id == 1 and media_type == "movie"


class _FakeTMDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def fetch_details(self, media_type: str, tmdb_id: int):
        self.calls.append((media_type, tmdb_id))

        class Response:
            ok = True
            status = 200
            text = ""
            data = {"external_ids": {"imdb_id": "tt1234567"}}

        return Response()


class _FakeSourceResponse:
    ok = True
    status = 200
    text = ""
    data = {
        "total_pages": 1,
        "results": [
            {"id": 1, "title": "Existing", "popularity": 1.0},
            {"id": 2, "title": "New", "popularity": 2.0},
        ],
    }


class _FakeSourceDB:
    def title_key_set(self):
        return {("movie", 1)}

    def should_harvest_many(self, *_args, **_kwargs):
        raise AssertionError("source scan should not do per-title TTL queries")


class _FakeSourceTMDB:
    source_scan_concurrency = 1

    async def fetch_source_page(self, _source, _page):
        return _FakeSourceResponse()


class _RecordingSourceTMDB:
    def __init__(self, response_fn):
        self.response_fn = response_fn
        self.calls: list[tuple[str, int]] = []

    async def fetch_source_page(self, source, page):
        self.calls.append((source.name, page))
        return self.response_fn(source, page)


def _source_stats(sources):
    return {
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
        for source in sources
    }


def _run_tmdb_scan(sources, tmdb, stats):
    return asyncio.run(
        scan_tmdb_sources(
            stop_event=asyncio.Event(),
            db=_FakeSourceDB(),
            tmdb_client=tmdb,
            tmdb_sources=sources,
            source_stats=stats,
            candidates=[],
            seen=set(),
            parse_int_fn=parse_int,
            candidate_cls=Candidate,
            publish_scan_progress_fn=lambda *_args, **_kwargs: None,
            logger=None,
            page_batch_concurrency=1,
            pages_scanned=0,
            effective_target_pages=sum(source.max_pages for source in sources),
            raw_seen_total=0,
        )
    )


def test_provider_wide_first_page_failure_halts_remaining_tmdb_sources():
    sources = [
        TMDBSource("movie/popular", "/movie/popular", "movie", 3),
        TMDBSource("tv/popular", "/tv/popular", "tv", 2),
    ]
    stats = _source_stats(sources)
    tmdb = _RecordingSourceTMDB(
        lambda _source, _page: APIResponse(
            status=401,
            headers={},
            data={"status_message": "Invalid API key"},
            text="Invalid API key",
        )
    )

    interrupted, pages_scanned, effective_pages, _raw = _run_tmdb_scan(sources, tmdb, stats)

    assert interrupted is False
    assert tmdb.calls == [("movie/popular", 1)]
    assert pages_scanned == 1
    assert effective_pages == 1
    assert stats["movie/popular"]["aborted"] == 1
    assert stats["movie/popular"]["abort_status"] == 401
    assert stats["movie/popular"]["pages_effective"] == 1
    assert stats["tv/popular"]["aborted"] == 1
    assert stats["tv/popular"]["abort_status"] == 401
    assert stats["tv/popular"]["pages_effective"] == 0


def test_endpoint_first_page_failure_skips_only_that_tmdb_source():
    sources = [
        TMDBSource("movie/popular", "/movie/popular", "movie", 3),
        TMDBSource("tv/popular", "/tv/popular", "tv", 2),
    ]
    stats = _source_stats(sources)

    def response_for(source, _page):
        if source.name == "movie/popular":
            return APIResponse(status=404, headers={}, data={}, text="not found")
        return APIResponse(
            status=200,
            headers={},
            data={"total_pages": 1, "results": []},
            text="",
        )

    tmdb = _RecordingSourceTMDB(response_for)

    interrupted, pages_scanned, effective_pages, _raw = _run_tmdb_scan(sources, tmdb, stats)

    assert interrupted is False
    assert tmdb.calls == [("movie/popular", 1), ("tv/popular", 1)]
    assert pages_scanned == 2
    assert effective_pages == 2
    assert stats["movie/popular"]["aborted"] == 1
    assert stats["movie/popular"]["abort_status"] == 404
    assert stats["tv/popular"]["pages_fetched"] == 1


def test_later_tmdb_page_failure_does_not_abort_remaining_pages():
    source = TMDBSource("movie/popular", "/movie/popular", "movie", 3)
    stats = _source_stats([source])

    def response_for(_source, page):
        if page == 2:
            return APIResponse(status=500, headers={}, data={}, text="temporary")
        return APIResponse(
            status=200,
            headers={},
            data={"total_pages": 3, "results": []},
            text="",
        )

    tmdb = _RecordingSourceTMDB(response_for)

    _run_tmdb_scan([source], tmdb, stats)

    assert tmdb.calls == [("movie/popular", 1), ("movie/popular", 2), ("movie/popular", 3)]
    assert stats["movie/popular"]["errors"] == 1
    assert stats["movie/popular"]["aborted"] == 0


def test_local_refresh_skips_tmdb_details_when_imdb_mapping_is_cached():
    tmdb = _FakeTMDB()
    candidates = [
        Candidate(1, "movie", "Cached", 0.0, harvest_reason="ttl"),
        Candidate(2, "movie", "Needs Details", 0.0, harvest_reason="ttl"),
    ]

    details, interrupted = asyncio.run(
        fetch_tmdb_details(
            candidates=candidates,
            stop_event=asyncio.Event(),
            tmdb_client=tmdb,
            db=_FakeDB(),
            details_concurrency=2,
            now_epoch_fn=lambda: 1000,
            logger=None,
        )
    )

    assert interrupted is False
    assert details[("movie", 1)] == {}
    assert details[("movie", 2)] == {"external_ids": {"imdb_id": "tt1234567"}}
    assert tmdb.calls == [("movie", 2)]


def test_source_scan_skips_existing_titles_from_memory_without_ttl_queries():
    source = TMDBSource("movie/popular", "/movie/popular", "movie", 1)
    stats = {
        source.name: {
            "pages_configured": 1,
            "pages_effective": 1,
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
        }
    }
    candidates: list[Candidate] = []

    interrupted, *_ = asyncio.run(
        scan_tmdb_sources(
            stop_event=asyncio.Event(),
            db=_FakeSourceDB(),
            tmdb_client=_FakeSourceTMDB(),
            tmdb_sources=[source],
            source_stats=stats,
            candidates=candidates,
            seen=set(),
            parse_int_fn=parse_int,
            candidate_cls=Candidate,
            publish_scan_progress_fn=lambda *_args, **_kwargs: None,
            logger=None,
            page_batch_concurrency=1,
            pages_scanned=0,
            effective_target_pages=1,
            raw_seen_total=0,
        )
    )

    assert interrupted is False
    assert [(candidate.tmdb_id, candidate.harvest_reason) for candidate in candidates] == [
        (2, "source")
    ]
    assert stats[source.name]["skipped"] == 1


def test_source_scan_logs_per_source_summary(caplog):
    source = TMDBSource("movie/popular", "/movie/popular", "movie", 1)
    stats = {
        source.name: {
            "pages_configured": 1,
            "pages_effective": 1,
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
        }
    }
    logger = logging.getLogger("betterer-ratings")

    with caplog.at_level(logging.INFO, logger="betterer-ratings"):
        asyncio.run(
            scan_tmdb_sources(
                stop_event=asyncio.Event(),
                db=_FakeSourceDB(),
                tmdb_client=_FakeSourceTMDB(),
                tmdb_sources=[source],
                source_stats=stats,
                candidates=[],
                seen=set(),
                parse_int_fn=parse_int,
                candidate_cls=Candidate,
                publish_scan_progress_fn=lambda *_args, **_kwargs: None,
                logger=logger,
                page_batch_concurrency=1,
                pages_scanned=0,
                effective_target_pages=1,
                raw_seen_total=0,
            )
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("[TMDB] Source scan complete" in message for message in messages)
    assert any("source=movie/popular" in message for message in messages)


class _FakeIMDbCache:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self, _imdb_id: str, _media_type: str):
        return self.value


def test_imdb_mapping_uses_mappings_then_titles_then_cache(local_db):
    db = local_db
    db._upsert_title(
        tmdb_id=10,
        media_type="movie",
        title="Mapped",
        imdb_id=None,
        popularity=1.0,
        tmdb_vote_average=None,
        now_ts=100,
        error_message=None,
    )
    db._upsert_mapping(10, "movie", "imdb", "tt1111111", 100)
    db._upsert_title(
        tmdb_id=11,
        media_type="movie",
        title="Title IMDb",
        imdb_id="tt2222222",
        popularity=2.0,
        tmdb_vote_average=None,
        now_ts=100,
        error_message=None,
    )

    from_mapping = resolve_imdb_to_tmdb_local(
        db=db,
        imdb_cache=_FakeIMDbCache((99, "Cache", 9.0)),
        imdb_id="tt1111111",
        media_type="movie",
        normalize_imdb_title_id_fn=normalize_imdb_title_id,
        parse_int_fn=parse_int,
    )
    from_title = resolve_imdb_to_tmdb_local(
        db=db,
        imdb_cache=_FakeIMDbCache((99, "Cache", 9.0)),
        imdb_id="tt2222222",
        media_type="movie",
        normalize_imdb_title_id_fn=normalize_imdb_title_id,
        parse_int_fn=parse_int,
    )
    from_cache = resolve_imdb_to_tmdb_local(
        db=db,
        imdb_cache=_FakeIMDbCache((99, "Cache", 9.0)),
        imdb_id="tt3333333",
        media_type="movie",
        normalize_imdb_title_id_fn=normalize_imdb_title_id,
        parse_int_fn=parse_int,
    )

    assert from_mapping and from_mapping.tmdb_id == 10
    assert from_title and from_title.tmdb_id == 11
    assert from_cache and from_cache.tmdb_id == 99
