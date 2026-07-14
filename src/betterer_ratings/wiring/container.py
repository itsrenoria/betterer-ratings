from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from betterer_ratings.config.schema import AppConfig, ensure_app_config
from betterer_ratings.infra.db.local_database import LocalDatabase
from betterer_ratings.infra.rate_limit.limiter import AsyncWindowLimiter
from betterer_ratings.infra.rate_limit.service_gate import ServiceGate
from betterer_ratings.providers.mdblist_client import MDBListClient
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.providers.tmdb_client import TMDBClient
from betterer_ratings.services.harvest.harvester import Harvester
from betterer_ratings.services.submit.submitter import Submitter


@dataclass
class AppContainer:
    config: AppConfig
    db_path: Path
    db: LocalDatabase
    tmdb_gate: ServiceGate
    mdblist_gate: ServiceGate
    pmdb_api_gate: ServiceGate
    pmdb_rating_gate: ServiceGate
    pmdb_mapping_gate: ServiceGate
    tmdb_client: TMDBClient
    mdblist_client: MDBListClient
    pmdb_client: PMDBClient
    harvester: Harvester
    submitter: Submitter


def build_container(*, config: AppConfig) -> AppContainer:
    app_config = ensure_app_config(config)

    db_path = Path(app_config.runtime.database_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = LocalDatabase(db_path)

    tmdb_gate = ServiceGate(
        "tmdb",
        db,
        AsyncWindowLimiter(
            max_requests=app_config.tmdb.rate_limit.requests,
            period_seconds=app_config.tmdb.rate_limit.per_seconds,
            name="tmdb",
        ),
    )

    mdblist_gate = ServiceGate(
        "mdblist",
        db,
        AsyncWindowLimiter(
            max_requests=app_config.mdblist.rate_limit.requests,
            period_seconds=app_config.mdblist.rate_limit.per_seconds,
            name="mdblist",
        ),
        daily_reserve=app_config.mdblist.daily_reserve,
    )

    pmdb_api_gate = ServiceGate(
        "pmdb_api",
        db,
        AsyncWindowLimiter(
            max_requests=app_config.pmdb.api_rate_limit.requests,
            period_seconds=app_config.pmdb.api_rate_limit.per_seconds,
            name="pmdb_api",
        ),
    )
    pmdb_rating_gate = ServiceGate(
        "pmdb_ratings",
        db,
        AsyncWindowLimiter(
            max_requests=app_config.pmdb.ratings_limit.requests,
            period_seconds=app_config.pmdb.ratings_limit.per_seconds,
            name="pmdb_ratings",
        ),
    )
    pmdb_mapping_gate = ServiceGate(
        "pmdb_mappings",
        db,
        AsyncWindowLimiter(
            max_requests=app_config.pmdb.mappings_limit.requests,
            period_seconds=app_config.pmdb.mappings_limit.per_seconds,
            name="pmdb_mappings",
        ),
    )

    tmdb_client = TMDBClient(
        api_key=app_config.api_keys.tmdb,
        config=app_config.tmdb,
        gate=tmdb_gate,
    )
    mdblist_client = MDBListClient(
        api_key=app_config.api_keys.mdblist,
        config=app_config.mdblist,
        gate=mdblist_gate,
    )
    pmdb_client = PMDBClient(
        api_key=app_config.api_keys.pmdb,
        config=app_config.pmdb,
        api_gate=pmdb_api_gate,
        rating_gate=pmdb_rating_gate,
        mapping_gate=pmdb_mapping_gate,
    )

    harvester = Harvester(
        config=app_config,
        db=db,
        tmdb_client=tmdb_client,
        mdblist_client=mdblist_client,
    )
    submitter = Submitter(
        config=app_config,
        db=db,
        pmdb_client=pmdb_client,
    )

    return AppContainer(
        config=app_config,
        db_path=db_path,
        db=db,
        tmdb_gate=tmdb_gate,
        mdblist_gate=mdblist_gate,
        pmdb_api_gate=pmdb_api_gate,
        pmdb_rating_gate=pmdb_rating_gate,
        pmdb_mapping_gate=pmdb_mapping_gate,
        tmdb_client=tmdb_client,
        mdblist_client=mdblist_client,
        pmdb_client=pmdb_client,
        harvester=harvester,
        submitter=submitter,
    )
