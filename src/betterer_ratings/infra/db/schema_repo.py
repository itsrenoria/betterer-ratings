from __future__ import annotations

import sqlite3
import time
from typing import Callable


def ensure_column(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row["name"]) for row in columns}
    if column_name in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS titles (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT,
                imdb_id TEXT,
                popularity REAL,
                tmdb_vote_average REAL,
                last_seen_at INTEGER NOT NULL,
                last_harvested_at INTEGER,
                last_mdblist_fetch_at INTEGER,
                last_error TEXT,
                PRIMARY KEY (tmdb_id, media_type)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                label TEXT NOT NULL,
                score REAL NOT NULL,
                fetched_at INTEGER NOT NULL,
                pmdb_item_id TEXT,
                pmdb_status TEXT NOT NULL DEFAULT 'pending',
                pmdb_claimed_at INTEGER,
                pmdb_submitted_at INTEGER,
                pmdb_attempts INTEGER NOT NULL DEFAULT 0,
                pmdb_last_error TEXT,
                pmdb_retry_after INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tmdb_id, media_type, label)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episode_ratings (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                label TEXT NOT NULL,
                score REAL NOT NULL,
                fetched_at INTEGER NOT NULL,
                imdb_parent_id TEXT,
                imdb_episode_id TEXT,
                votes INTEGER,
                pmdb_item_id TEXT,
                pmdb_status TEXT NOT NULL DEFAULT 'pending',
                pmdb_claimed_at INTEGER,
                pmdb_submitted_at INTEGER,
                pmdb_attempts INTEGER NOT NULL DEFAULT 0,
                pmdb_last_error TEXT,
                pmdb_retry_after INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tmdb_id, media_type, season, episode, label)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mappings (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                id_type TEXT NOT NULL,
                id_value TEXT NOT NULL,
                fetched_at INTEGER NOT NULL,
                pmdb_item_id TEXT,
                pmdb_item_value TEXT,
                pmdb_status TEXT NOT NULL DEFAULT 'pending',
                pmdb_claimed_at INTEGER,
                pmdb_submitted_at INTEGER,
                pmdb_attempts INTEGER NOT NULL DEFAULT 0,
                pmdb_last_error TEXT,
                pmdb_retry_after INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tmdb_id, media_type, id_type)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_state (
                service TEXT PRIMARY KEY,
                paused_until INTEGER DEFAULT 0,
                pause_reason TEXT,
                rate_limit INTEGER,
                rate_remaining INTEGER,
                rate_reset INTEGER,
                last_status INTEGER,
                updated_at INTEGER
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submitted_titles (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                first_submitted_at INTEGER NOT NULL,
                last_submitted_at INTEGER NOT NULL,
                PRIMARY KEY (tmdb_id, media_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submitted_title_days (
                day_key TEXT NOT NULL,
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                PRIMARY KEY (day_key, tmdb_id, media_type)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submitted_title_days_day
            ON submitted_title_days (day_key)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ratings_pending
            ON ratings (pmdb_status, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_ratings_pending
            ON episode_ratings (pmdb_status, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_ratings_parent_lookup
            ON episode_ratings (imdb_parent_id, season, episode)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mappings_pending
            ON mappings (pmdb_status, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mappings_imdb_lookup
            ON mappings (id_type, id_value, media_type)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_titles_imdb_lookup
            ON titles (imdb_id, media_type)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_titles_harvest_due
            ON titles (last_harvested_at, media_type, tmdb_id)
            """
        )

        ensure_column(conn, table_name="ratings", column_name="pmdb_item_id", column_type="TEXT")
        ensure_column(conn, table_name="mappings", column_name="pmdb_item_id", column_type="TEXT")
        ensure_column(
            conn, table_name="ratings", column_name="pmdb_claimed_at", column_type="INTEGER"
        )
        ensure_column(
            conn, table_name="mappings", column_name="pmdb_claimed_at", column_type="INTEGER"
        )
        ensure_column(
            conn, table_name="episode_ratings", column_name="pmdb_item_id", column_type="TEXT"
        )
        ensure_column(
            conn, table_name="episode_ratings", column_name="pmdb_claimed_at", column_type="INTEGER"
        )


def _migration_002_queue_claim_indexes(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ratings_claim_order
            ON ratings (pmdb_status, fetched_at, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mappings_claim_order
            ON mappings (pmdb_status, fetched_at, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_ratings_claim_order
            ON episode_ratings (pmdb_status, fetched_at, pmdb_retry_after)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_ratings_claim_batch
            ON episode_ratings (
                pmdb_status,
                tmdb_id,
                media_type,
                season,
                pmdb_retry_after,
                episode,
                fetched_at
            )
            """
        )


def _migration_003_mapping_item_value(conn: sqlite3.Connection) -> None:
    with conn:
        ensure_column(
            conn,
            table_name="mappings",
            column_name="pmdb_item_value",
            column_type="TEXT",
        )


MIGRATIONS: tuple[tuple[int, str, Callable[[sqlite3.Connection], None]], ...] = (
    (1, "initial_schema", _migration_001_initial_schema),
    (2, "queue_claim_indexes", _migration_002_queue_claim_indexes),
    (3, "mapping_item_value", _migration_003_mapping_item_value),
)


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at INTEGER NOT NULL
            )
            """
        )


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def apply_migrations(conn: sqlite3.Connection) -> int:
    ensure_migrations_table(conn)
    applied = get_applied_versions(conn)

    last_applied = 0
    for version, name, migration_fn in MIGRATIONS:
        if version in applied:
            last_applied = max(last_applied, version)
            continue

        migration_fn(conn)
        with conn:
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, int(time.time())),
            )
        last_applied = max(last_applied, version)

    return last_applied


def init_schema(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)
