from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import betterer_ratings.constants as package_constants


class ConfigValidationError(ValueError):
    pass


def _require_mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigValidationError(f"{path} must be a table")
    return value


def _require_list(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigValidationError(f"{path} must be an array")
    return value


def _as_str(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise ConfigValidationError(f"{path} must be a string")
    return value


def _as_int(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{path} must be an integer")
    return int(value)


def _as_float(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"{path} must be a number")
    return float(value)


def _reject_unknown_keys(value: Mapping[str, Any], *, allowed: set[str], path: str) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ConfigValidationError(f"Unknown key(s) at {path}: {', '.join(unknown)}")


@dataclass(frozen=True)
class RateLimitConfig:
    requests: int
    per_seconds: float

    @staticmethod
    def from_mapping(value: Mapping[str, Any], *, path: str) -> "RateLimitConfig":
        _reject_unknown_keys(value, allowed={"requests", "per_seconds"}, path=path)
        return RateLimitConfig(
            requests=max(1, _as_int(value.get("requests"), path=f"{path}.requests")),
            per_seconds=max(0.001, _as_float(value.get("per_seconds"), path=f"{path}.per_seconds")),
        )


@dataclass(frozen=True)
class APIKeysConfig:
    tmdb: str
    mdblist: str
    pmdb: str

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "APIKeysConfig":
        _reject_unknown_keys(value, allowed={"tmdb", "mdblist", "pmdb"}, path="api_keys")
        tmdb = _as_str(value.get("tmdb"), path="api_keys.tmdb").strip()
        mdblist = _as_str(value.get("mdblist"), path="api_keys.mdblist").strip()
        pmdb = _as_str(value.get("pmdb"), path="api_keys.pmdb").strip()

        missing = []
        for key, actual in (
            ("api_keys.tmdb", tmdb),
            ("api_keys.mdblist", mdblist),
            ("api_keys.pmdb", pmdb),
        ):
            lowered = actual.lower()
            if not actual or lowered.startswith("replace-me") or lowered.startswith("your_"):
                missing.append(key)

        if missing:
            raise ConfigValidationError(
                "Missing required API keys in config: " + ", ".join(sorted(missing))
            )

        return APIKeysConfig(tmdb=tmdb, mdblist=mdblist, pmdb=pmdb)


@dataclass(frozen=True)
class RuntimeConfig:
    log_level: str
    source_scan_interval_hours: int
    title_refresh_days: int
    episode_refresh_days: int
    failed_retry_days: int

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "RuntimeConfig":
        _reject_unknown_keys(
            value,
            allowed={
                "log_level",
                "source_scan_interval_hours",
                "title_refresh_days",
                "episode_refresh_days",
                "failed_retry_days",
            },
            path="worker",
        )
        log_level = _as_str(value.get("log_level"), path="worker.log_level").strip().upper()
        if log_level not in package_constants.SUPPORTED_LOG_LEVELS:
            allowed = ", ".join(sorted(package_constants.SUPPORTED_LOG_LEVELS))
            raise ConfigValidationError(f"Invalid worker.log_level. Expected one of: {allowed}")

        return RuntimeConfig(
            log_level=log_level,
            source_scan_interval_hours=max(
                1,
                _as_int(
                    value.get("source_scan_interval_hours"),
                    path="worker.source_scan_interval_hours",
                ),
            ),
            title_refresh_days=max(
                1, _as_int(value.get("title_refresh_days"), path="worker.title_refresh_days")
            ),
            episode_refresh_days=max(
                1, _as_int(value.get("episode_refresh_days"), path="worker.episode_refresh_days")
            ),
            failed_retry_days=max(
                0, _as_int(value.get("failed_retry_days"), path="worker.failed_retry_days")
            ),
        )

    @property
    def database_path(self) -> str:
        return package_constants.CONTAINER_DATABASE_PATH

    @property
    def imdb_archive_path(self) -> str:
        return package_constants.CONTAINER_IMDB_ARCHIVE_PATH

    @property
    def temp_path(self) -> str:
        return package_constants.CONTAINER_TEMP_PATH

    @property
    def imdb_archive_download_timeout_seconds(self) -> int:
        return 300

    @property
    def harvester_cycle_sleep_seconds(self) -> int:
        return 30

    @property
    def submitter_poll_seconds(self) -> float:
        return 0.25

    @property
    def submitter_workers(self) -> int:
        return 16

    @property
    def submitter_in_flight_lease_seconds(self) -> int:
        return 300

    @property
    def submitter_max_retry_attempts(self) -> int:
        return 12


@dataclass(frozen=True)
class TMDBSourceConfig:
    name: str
    max_pages: int

    @staticmethod
    def from_mapping(value: Mapping[str, Any], *, path: str) -> "TMDBSourceConfig":
        _reject_unknown_keys(value, allowed={"name", "max_pages"}, path=path)
        name = _as_str(value.get("name"), path=f"{path}.name").strip().lower().lstrip("/")
        if name not in package_constants.SUPPORTED_TMDB_SOURCE_NAMES:
            raise ConfigValidationError(f"Unsupported TMDB source name: {name}")
        return TMDBSourceConfig(
            name=name,
            max_pages=max(1, min(500, _as_int(value.get("max_pages"), path=f"{path}.max_pages"))),
        )


@dataclass(frozen=True)
class TMDBConfig:
    language: str
    rate_limit: RateLimitConfig
    sources: tuple[TMDBSourceConfig, ...]

    @property
    def base_url(self) -> str:
        return "https://api.themoviedb.org/3"

    @property
    def timeout_seconds(self) -> int:
        return 30

    @property
    def max_retries(self) -> int:
        return 5

    @property
    def details_concurrency(self) -> int:
        return 16

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "TMDBConfig":
        _reject_unknown_keys(value, allowed={"language", "rate_limit", "sources"}, path="tmdb")
        sources_raw = _require_list(value.get("sources"), path="tmdb.sources")
        if not sources_raw:
            raise ConfigValidationError("tmdb.sources must be a non-empty array")
        sources = tuple(
            TMDBSourceConfig.from_mapping(
                _require_mapping(item, path=f"tmdb.sources[{idx}]"), path=f"tmdb.sources[{idx}]"
            )
            for idx, item in enumerate(sources_raw)
        )
        return TMDBConfig(
            language=_as_str(value.get("language"), path="tmdb.language"),
            rate_limit=RateLimitConfig.from_mapping(
                _require_mapping(value.get("rate_limit"), path="tmdb.rate_limit"),
                path="tmdb.rate_limit",
            ),
            sources=sources,
        )


@dataclass(frozen=True)
class IMDbConfig:
    min_votes: int
    types: tuple[str, ...]
    exclude_unknown_year: bool

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "IMDbConfig":
        _reject_unknown_keys(
            value,
            allowed={"min_votes", "types", "exclude_unknown_year"},
            path="imdb",
        )
        types_raw = _require_list(value.get("types"), path="imdb.types")
        normalized_types: list[str] = []
        for idx, item in enumerate(types_raw):
            title_type = _as_str(item, path=f"imdb.types[{idx}]").strip()
            lowered = title_type.lower()
            if lowered not in package_constants.SUPPORTED_IMDB_TITLE_TYPES:
                allowed = ", ".join(sorted(package_constants.SUPPORTED_IMDB_TITLE_TYPES))
                raise ConfigValidationError(
                    f"Invalid imdb.types[{idx}] value '{title_type}'. Expected one of: {allowed}"
                )
            canonical = {
                "movie": "movie",
                "tvseries": "tvSeries",
                "tvminiseries": "tvMiniSeries",
                "tvmovie": "tvMovie",
            }[lowered]
            if canonical not in normalized_types:
                normalized_types.append(canonical)
        if not normalized_types:
            normalized_types = list(package_constants.DEFAULT_IMDB_TITLE_TYPES)
        exclude_unknown_year = value.get("exclude_unknown_year")
        if not isinstance(exclude_unknown_year, bool):
            raise ConfigValidationError("imdb.exclude_unknown_year must be a boolean")
        return IMDbConfig(
            min_votes=max(0, _as_int(value.get("min_votes"), path="imdb.min_votes")),
            types=tuple(normalized_types),
            exclude_unknown_year=exclude_unknown_year,
        )


@dataclass(frozen=True)
class MDBListConfig:
    rate_limit: RateLimitConfig
    batch_size: int
    daily_reserve: int = 0

    @property
    def base_url(self) -> str:
        return "https://api.mdblist.com"

    @property
    def timeout_seconds(self) -> int:
        return 30

    @property
    def max_retries(self) -> int:
        return 5

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "MDBListConfig":
        _reject_unknown_keys(
            value, allowed={"rate_limit", "batch_size", "daily_reserve"}, path="mdblist"
        )
        daily_reserve = 0
        if value.get("daily_reserve") is not None:
            daily_reserve = max(
                0, min(999, _as_int(value.get("daily_reserve"), path="mdblist.daily_reserve"))
            )
        return MDBListConfig(
            rate_limit=RateLimitConfig.from_mapping(
                _require_mapping(value.get("rate_limit"), path="mdblist.rate_limit"),
                path="mdblist.rate_limit",
            ),
            batch_size=max(1, min(200, _as_int(value.get("batch_size"), path="mdblist.batch_size"))),
            daily_reserve=daily_reserve,
        )


@dataclass(frozen=True)
class PMDBConfig:
    api_rate_limit: RateLimitConfig
    ratings_limit: RateLimitConfig
    mappings_limit: RateLimitConfig

    @property
    def base_url(self) -> str:
        return "https://publicmetadb.com"

    @property
    def timeout_seconds(self) -> int:
        return 30

    @property
    def max_retries(self) -> int:
        return 5

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "PMDBConfig":
        _reject_unknown_keys(
            value,
            allowed={"api_rate_limit", "ratings_limit", "mappings_limit"},
            path="pmdb",
        )
        return PMDBConfig(
            api_rate_limit=RateLimitConfig.from_mapping(
                _require_mapping(value.get("api_rate_limit"), path="pmdb.api_rate_limit"),
                path="pmdb.api_rate_limit",
            ),
            ratings_limit=RateLimitConfig.from_mapping(
                _require_mapping(value.get("ratings_limit"), path="pmdb.ratings_limit"),
                path="pmdb.ratings_limit",
            ),
            mappings_limit=RateLimitConfig.from_mapping(
                _require_mapping(value.get("mappings_limit"), path="pmdb.mappings_limit"),
                path="pmdb.mappings_limit",
            ),
        )


@dataclass(frozen=True)
class AppConfig:
    api_keys: APIKeysConfig
    runtime: RuntimeConfig
    tmdb: TMDBConfig
    imdb: IMDbConfig
    mdblist: MDBListConfig
    pmdb: PMDBConfig

    @staticmethod
    def from_mapping(value: Mapping[str, Any]) -> "AppConfig":
        _reject_unknown_keys(
            value,
            allowed={"api_keys", "worker", "tmdb", "imdb", "mdblist", "pmdb"},
            path="<root>",
        )
        api_keys = APIKeysConfig.from_mapping(
            _require_mapping(value.get("api_keys"), path="api_keys")
        )
        runtime = RuntimeConfig.from_mapping(_require_mapping(value.get("worker"), path="worker"))
        tmdb = TMDBConfig.from_mapping(_require_mapping(value.get("tmdb"), path="tmdb"))
        imdb = IMDbConfig.from_mapping(_require_mapping(value.get("imdb"), path="imdb"))
        mdblist = MDBListConfig.from_mapping(_require_mapping(value.get("mdblist"), path="mdblist"))
        pmdb = PMDBConfig.from_mapping(_require_mapping(value.get("pmdb"), path="pmdb"))
        return AppConfig(
            api_keys=api_keys,
            runtime=runtime,
            tmdb=tmdb,
            imdb=imdb,
            mdblist=mdblist,
            pmdb=pmdb,
        )


def ensure_app_config(config: Any) -> AppConfig:
    if isinstance(config, AppConfig):
        return config
    if isinstance(config, Mapping):
        return AppConfig.from_mapping(config)
    raise TypeError(f"Unsupported config type: {type(config)!r}")
