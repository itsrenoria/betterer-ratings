from __future__ import annotations

import sqlite3

from tests import support as m


def test_configured_database_filename_is_betterer_ratings_not_legacy_mdblist_pmdb():
    db_filename = m.constants.CONTAINER_DATABASE_PATH.rsplit("/", 1)[-1]
    assert db_filename == "betterer_ratings.sqlite3"
    assert db_filename != "mdblist_pmdb.sqlite3"


def test_local_database_at_betterer_ratings_filename_initializes_and_stores_data(tmp_path):
    db_filename = m.constants.CONTAINER_DATABASE_PATH.rsplit("/", 1)[-1]
    db = m.LocalDatabase(tmp_path / db_filename)
    try:
        db.save_enriched_item(
            tmdb_id=1,
            media_type="movie",
            title="Title 1",
            imdb_id="tt1000000",
            popularity=1.0,
            tmdb_vote_average=70.0,
            enrichment_error=None,
            ratings={"IM": 70.0},
            mappings={"tvdb": "1"},
            now_ts=100,
        )
        db.save_imdb_episode_ratings(
            tmdb_id=2,
            media_type="tv",
            imdb_parent_id="tt2000000",
            entries=[
                m.IMDbEpisodeArchiveCandidate(
                    parent_imdb_id="tt2000000",
                    episode_imdb_id="tt2000001",
                    season=1,
                    episode=1,
                    score=80.0,
                    votes=100,
                )
            ],
            now_ts=100,
        )
        db.set_state("last_harvest_cycle_at", 100)

        for table in ("titles", "ratings", "mappings", "episode_ratings", "state"):
            count = db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count > 0
    finally:
        db.close()


def test_mappings_schema_tracks_the_value_associated_with_a_cached_pmdb_id(local_db):
    columns = {
        str(row["name"]): str(row["type"])
        for row in local_db.conn.execute("PRAGMA table_info(mappings)").fetchall()
    }

    assert columns["pmdb_item_value"] == "TEXT"
    applied = local_db.conn.execute(
        "SELECT name FROM schema_migrations WHERE version = 3"
    ).fetchone()
    assert applied is not None
    assert str(applied["name"]) == "mapping_item_value"


def test_version_two_database_adds_mapping_item_value_without_rebuilding(tmp_path):
    db_path = tmp_path / "version-two.sqlite3"
    db = m.LocalDatabase(db_path)
    db.close()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE mappings DROP COLUMN pmdb_item_value")
        conn.execute("DELETE FROM schema_migrations WHERE version = 3")
        conn.commit()
    finally:
        conn.close()

    migrated = m.LocalDatabase(db_path)
    try:
        columns = {
            str(row["name"])
            for row in migrated.conn.execute("PRAGMA table_info(mappings)").fetchall()
        }
        assert "pmdb_item_value" in columns
        assert migrated.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 3"
        ).fetchone()
    finally:
        migrated.close()
