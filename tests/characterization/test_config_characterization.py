from __future__ import annotations

from pathlib import Path

import pytest

from betterer_ratings.config.loader import load_config, parse_toml
from betterer_ratings.config.schema import AppConfig, ConfigValidationError
from betterer_ratings.constants import (
    CONTAINER_DATABASE_PATH,
    CONTAINER_IMDB_ARCHIVE_PATH,
    CONTAINER_TEMP_PATH,
    DEFAULT_CONFIG,
)


def test_simplified_config_loads_with_container_defaults(base_valid_config, write_config):
    config_path = write_config(base_valid_config)

    loaded = load_config(config_path)

    assert loaded.runtime.database_path == CONTAINER_DATABASE_PATH
    assert loaded.runtime.imdb_archive_path == CONTAINER_IMDB_ARCHIVE_PATH
    assert loaded.runtime.temp_path == CONTAINER_TEMP_PATH
    assert loaded.runtime.title_refresh_days == 7
    assert loaded.runtime.episode_refresh_days == 1
    assert loaded.runtime.submitter_workers == 16
    assert loaded.runtime.submitter_poll_seconds == 0.25
    assert loaded.tmdb.details_concurrency == 16
    assert loaded.tmdb.timeout_seconds == 30


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("worker", "discovery_mode", "hybrid"),
        ("worker", "daily_title_limit", 100000),
        ("worker", "console_mode", "dashboard"),
        ("worker", "log_file_path", "logs/betterer_ratings.log"),
        ("tmdb", "details_concurrency", 16),
    ],
)
def test_removed_config_keys_are_rejected(base_valid_config, write_config, section, key, value):
    base_valid_config[section][key] = value
    config_path = write_config(base_valid_config)

    with pytest.raises(ConfigValidationError, match="Unknown key"):
        load_config(config_path)


def test_repository_config_example_uses_simplified_schema():
    repo_root = Path(__file__).resolve().parents[2]

    payload = dict(parse_toml(repo_root / "config.example.toml"))
    payload["api_keys"] = {"tmdb": "tmdb-key", "mdblist": "mdblist-key", "pmdb": "pmdb-key"}
    loaded = AppConfig.from_mapping(payload)

    assert loaded.runtime.source_scan_interval_hours == 1
    assert loaded.runtime.title_refresh_days == 7
    assert loaded.runtime.episode_refresh_days == 1
    assert len(loaded.tmdb.sources) == 10
    assert loaded.tmdb.rate_limit.requests == 40
    assert loaded.mdblist.batch_size == 200


def test_repository_defaults_use_documented_provider_limits():
    assert DEFAULT_CONFIG["tmdb"]["rate_limit"]["requests"] == 40
    assert DEFAULT_CONFIG["mdblist"]["batch_size"] == 200


def test_config_objects_do_not_expose_removed_feature_shims(base_valid_config):
    loaded = AppConfig.from_mapping(base_valid_config)

    assert not hasattr(loaded, "public_dict")
    assert not hasattr(loaded.runtime, "to_dict")
    assert not hasattr(loaded.runtime, "ratings_refresh_days")
    assert not hasattr(loaded.runtime, "failed_refresh_days")
    assert not hasattr(loaded.runtime, "imdb_archive_auto_update_enabled")
    assert not hasattr(loaded.tmdb.sources[0], "enabled")
