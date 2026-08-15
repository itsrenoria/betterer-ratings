from __future__ import annotations

import re
from typing import Any, Dict, Set, Tuple

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_keys": {
        "tmdb": "",
        "mdblist": "",
        "pmdb": "",
    },
    "worker": {
        "log_level": "INFO",
        "source_scan_interval_hours": 1,
        "title_refresh_days": 7,
        "episode_refresh_days": 1,
        "failed_retry_days": 7,
    },
    "tmdb": {
        "language": "en-US",
        "rate_limit": {
            "requests": 40,
            "per_seconds": 1,
        },
        "sources": [
            {"name": "movie/popular", "max_pages": 500},
            {"name": "movie/top_rated", "max_pages": 500},
            {"name": "tv/popular", "max_pages": 500},
            {"name": "tv/top_rated", "max_pages": 500},
            {"name": "trending/movie/day", "max_pages": 500},
            {"name": "trending/movie/week", "max_pages": 500},
            {"name": "trending/tv/day", "max_pages": 500},
            {"name": "trending/tv/week", "max_pages": 500},
            {"name": "trending/all/day", "max_pages": 500},
            {"name": "trending/all/week", "max_pages": 500},
        ],
    },
    "imdb": {
        "min_votes": 250,
        "types": ["movie", "tvSeries", "tvMiniSeries", "tvMovie"],
        "exclude_unknown_year": True,
    },
    "mdblist": {
        "batch_size": 200,
        "rate_limit": {
            "requests": 2,
            "per_seconds": 1,
        },
    },
    "pmdb": {
        "api_rate_limit": {
            "requests": 15,
            "per_seconds": 1,
        },
        "ratings_limit": {
            "requests": 15,
            "per_seconds": 1,
        },
        "mappings_limit": {
            "requests": 15,
            "per_seconds": 1,
        },
    },
}

CONTAINER_DATABASE_PATH = "/data/db/betterer_ratings.sqlite3"
CONTAINER_IMDB_ARCHIVE_PATH = "/data/imdb"
CONTAINER_TEMP_PATH = "/data/temp"
IMDB_ARCHIVE_TITLE_BATCH_SIZE = 100_000
IMDB_ARCHIVE_REFRESH_HOUR_UTC = 13

SUPPORTED_PMDB_MAPPING_TYPES: Tuple[str, ...] = (
    "imdb",
    "tvdb",
    "mal",
    "anilist",
    "anidb",
    "trakt",
)

SUPPORTED_TMDB_SOURCE_NAMES: Set[str] = {
    "movie/popular",
    "movie/top_rated",
    "movie/now_playing",
    "movie/upcoming",
    "tv/popular",
    "tv/top_rated",
    "tv/on_the_air",
    "tv/airing_today",
    "trending/movie/day",
    "trending/movie/week",
    "trending/tv/day",
    "trending/tv/week",
    "trending/all/day",
    "trending/all/week",
}

IMDB_ARCHIVE_SOURCE_NAME = "imdb_archive"
SUPPORTED_IMDB_TITLE_TYPES: Set[str] = {
    "movie",
    "tvseries",
    "tvminiseries",
    "tvmovie",
}
DEFAULT_IMDB_TITLE_TYPES: Tuple[str, ...] = (
    "movie",
    "tvSeries",
    "tvMiniSeries",
    "tvMovie",
)
IMDB_TITLE_ID_RE = re.compile(r"^tt[0-9]+$")

HARVEST_REASON_ORDER: Tuple[str, ...] = ("failed", "ttl", "new")

SUPPORTED_LOG_LEVELS: Set[str] = {
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
}

RETRY_STORM_CLEANUP_KEY = "submitter_retry_storm_cleanup_v1"
IMDB_ARCHIVE_FINGERPRINT_KEY = "imdb_archive_fingerprint_v1"
IMDB_ARCHIVE_CURSOR_LINE_KEY = "imdb_archive_cursor_line_v1"
IMDB_ARCHIVE_CURSOR_BYTE_KEY = "imdb_archive_cursor_byte_v1"
IMDB_ARCHIVE_TOTAL_KEY = "imdb_archive_total_v1"
IMDB_ARCHIVE_EXHAUSTED_KEY = "imdb_archive_exhausted_v1"
IMDB_ARCHIVE_LAST_UPDATE_KEY = "imdb_archive_last_update_v1"
IMDB_ARCHIVE_LAST_UPDATE_ATTEMPT_KEY = "imdb_archive_last_update_attempt_v1"
IMDB_EPISODE_ARCHIVE_FINGERPRINT_KEY = "imdb_episode_archive_fingerprint_v1"
IMDB_EPISODE_ARCHIVE_CURSOR_LINE_KEY = "imdb_episode_archive_cursor_line_v1"
IMDB_EPISODE_ARCHIVE_CURSOR_BYTE_KEY = "imdb_episode_archive_cursor_byte_v1"
IMDB_EPISODE_ARCHIVE_TOTAL_KEY = "imdb_episode_archive_total_v1"
IMDB_EPISODE_ARCHIVE_EXHAUSTED_KEY = "imdb_episode_archive_exhausted_v1"
IMDB_EPISODE_LAST_FULL_SCAN_KEY = "imdb_episode_last_full_scan_v1"
IMDB_ARCHIVE_RETRY_COOLDOWN_SECONDS = 21600  # 6 hours
SOURCE_SCAN_LAST_RUN_KEY = "source_scan_last_run_v1"
IMDB_ARCHIVE_DATASET_URLS: Dict[str, str] = {
    "title.basics.tsv": "https://datasets.imdbws.com/title.basics.tsv.gz",
    "title.ratings.tsv": "https://datasets.imdbws.com/title.ratings.tsv.gz",
}
IMDB_EPISODE_DATASET_URLS: Dict[str, str] = {
    "title.episode.tsv": "https://datasets.imdbws.com/title.episode.tsv.gz",
}
