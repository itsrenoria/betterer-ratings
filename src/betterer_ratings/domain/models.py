from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass
class APIResponse:
    status: int
    headers: Dict[str, str]
    data: Any
    text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Candidate:
    tmdb_id: int
    media_type: str  # 'movie' or 'tv'
    title: str
    popularity: float
    harvest_reason: str = ""


@dataclass
class TMDBSource:
    name: str
    endpoint: str
    media_type_hint: Optional[str]
    max_pages: int


@dataclass
class IMDbArchiveSource:
    name: str
    titles_enabled: bool
    episodes_enabled: bool
    min_votes: int
    types: Tuple[str, ...]
    exclude_unknown_year: bool
    title_batch_size: int
    path: Path
    max_pages: int = 1


@dataclass
class IMDbArchiveCandidate:
    imdb_id: str
    media_type: str
    num_votes: int
    average_rating: Optional[float]


@dataclass
class IMDbEpisodeArchiveCandidate:
    parent_imdb_id: str
    episode_imdb_id: str
    season: int
    episode: int
    score: float
    votes: int


@dataclass
class HarvestCycleResult:
    selected_candidates: int
    tmdb_list_request_errors: int
    mdblist_request_failures: int
    interrupted: bool = False


@dataclass
class PMDBSubmitResult:
    success: bool
    retryable: bool
    retry_after_seconds: int
    duplicate_or_exists: bool
    error_text: str
    item_id: Optional[str]
    status_code: int = 0
    error_code: str = ""
    endpoint: str = ""
    stale_cached_item_id: bool = False


@dataclass
class PMDBDeleteResult:
    success: bool
    retryable: bool
    retry_after_seconds: int
    error_text: str
    status_code: int = 0
    error_code: str = ""
    endpoint: str = ""
