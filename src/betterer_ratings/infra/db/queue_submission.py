from __future__ import annotations

import sqlite3
from typing import Callable, Optional


def record_submission_success(
    conn: sqlite3.Connection,
    *,
    kind: str,
    tmdb_id: int,
    media_type: str,
    submitted_at: int,
    local_day_key_fn: Callable[[int], str],
) -> None:
    normalized_kind = str(kind).strip().lower()
    if normalized_kind in {"ratings", "rating"}:
        kind_name = "ratings"
    elif normalized_kind in {"episode_ratings", "episode_rating", "episodes"}:
        kind_name = "episode_ratings"
    else:
        kind_name = "mappings"
    day = local_day_key_fn(submitted_at)
    total_key = f"metrics:pmdb_submitted:{kind_name}:total"
    day_key = f"metrics:pmdb_submitted:{kind_name}:day:{day}"

    conn.execute(
        """
        INSERT INTO state(key, value) VALUES(?, '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(state.value AS INTEGER) + 1
        """,
        (total_key,),
    )
    conn.execute(
        """
        INSERT INTO state(key, value) VALUES(?, '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(state.value AS INTEGER) + 1
        """,
        (day_key,),
    )

    if kind_name == "episode_ratings":
        return

    conn.execute(
        """
        INSERT INTO submitted_titles(
            tmdb_id, media_type, first_submitted_at, last_submitted_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(tmdb_id, media_type) DO UPDATE SET
            last_submitted_at=excluded.last_submitted_at
        """,
        (tmdb_id, media_type, submitted_at, submitted_at),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO submitted_title_days(day_key, tmdb_id, media_type)
        VALUES (?, ?, ?)
        """,
        (day, tmdb_id, media_type),
    )


def record_episode_submission_success(
    conn: sqlite3.Connection,
    *,
    submitted_at: int,
    local_day_key_fn: Callable[[int], str],
) -> None:
    record_submission_success(
        conn,
        kind="episode_ratings",
        tmdb_id=0,
        media_type="tv",
        submitted_at=submitted_at,
        local_day_key_fn=local_day_key_fn,
    )


def mark_rating_submitted(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    submitted_at: int,
    pmdb_item_id: Optional[str] = None,
    local_day_key_fn: Callable[[int], str],
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE ratings
            SET pmdb_status = 'submitted',
                pmdb_item_id = COALESCE(?, pmdb_item_id),
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = ?,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (pmdb_item_id, submitted_at, tmdb_id, media_type, label),
        )
        record_submission_success(
            conn,
            kind="ratings",
            tmdb_id=tmdb_id,
            media_type=media_type,
            submitted_at=submitted_at,
            local_day_key_fn=local_day_key_fn,
        )


def mark_mapping_submitted(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    submitted_at: int,
    pmdb_item_id: Optional[str] = None,
    local_day_key_fn: Callable[[int], str],
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE mappings
            SET pmdb_status = 'submitted',
                pmdb_item_id = ?,
                pmdb_item_value = CASE WHEN ? IS NULL THEN NULL ELSE id_value END,
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = ?,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (pmdb_item_id, pmdb_item_id, submitted_at, tmdb_id, media_type, id_type),
        )
        record_submission_success(
            conn,
            kind="mappings",
            tmdb_id=tmdb_id,
            media_type=media_type,
            submitted_at=submitted_at,
            local_day_key_fn=local_day_key_fn,
        )


def mark_episode_rating_submitted(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
    submitted_at: int,
    pmdb_item_id: Optional[str] = None,
    local_day_key_fn: Callable[[int], str],
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE episode_ratings
            SET pmdb_status = 'submitted',
                pmdb_item_id = COALESCE(?, pmdb_item_id),
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = ?,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            (
                pmdb_item_id,
                int(submitted_at),
                int(tmdb_id),
                str(media_type),
                int(season),
                int(episode),
                str(label),
            ),
        )
        record_episode_submission_success(
            conn,
            submitted_at=submitted_at,
            local_day_key_fn=local_day_key_fn,
        )
