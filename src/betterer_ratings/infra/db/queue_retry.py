from __future__ import annotations

import sqlite3


def mark_rating_retry(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    retry_after: int,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE ratings
            SET pmdb_status = 'retry',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL,
                pmdb_retry_after = ?
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (error_text, retry_after, tmdb_id, media_type, label),
        )


def mark_mapping_retry(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    retry_after: int,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE mappings
            SET pmdb_status = 'retry',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL,
                pmdb_retry_after = ?
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (error_text, retry_after, tmdb_id, media_type, id_type),
        )


def mark_episode_rating_retry(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
    retry_after: int,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE episode_ratings
            SET pmdb_status = 'retry',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL,
                pmdb_retry_after = ?
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            (
                str(error_text),
                int(retry_after),
                int(tmdb_id),
                str(media_type),
                int(season),
                int(episode),
                str(label),
            ),
        )


def clear_rating_pmdb_item_id(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE ratings
            SET pmdb_item_id = NULL
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (tmdb_id, media_type, label),
        )


def clear_mapping_pmdb_item_id(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE mappings
            SET pmdb_item_id = NULL,
                pmdb_item_value = NULL
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (tmdb_id, media_type, id_type),
        )


def clear_episode_rating_pmdb_item_id(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE episode_ratings
            SET pmdb_item_id = NULL
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            (tmdb_id, media_type, season, episode, label),
        )


def mark_rating_failed(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE ratings
            SET pmdb_status = 'failed',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (error_text, tmdb_id, media_type, label),
        )


def mark_mapping_failed(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE mappings
            SET pmdb_status = 'failed',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (error_text, tmdb_id, media_type, id_type),
        )


def mark_episode_rating_failed(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
    error_text: str,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE episode_ratings
            SET pmdb_status = 'failed',
                pmdb_attempts = pmdb_attempts + 1,
                pmdb_last_error = ?,
                pmdb_claimed_at = NULL
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            (
                str(error_text),
                int(tmdb_id),
                str(media_type),
                int(season),
                int(episode),
                str(label),
            ),
        )
