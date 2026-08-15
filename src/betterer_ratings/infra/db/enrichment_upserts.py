from __future__ import annotations

import sqlite3
from typing import Any, Callable, Optional


def upsert_title(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    title: str,
    imdb_id: Optional[str],
    popularity: float,
    tmdb_vote_average: Optional[float],
    now_ts: int,
    error_message: Optional[str],
    normalize_imdb_title_id_fn: Callable[[Any], Optional[str]],
) -> None:
    existing = conn.execute(
        "SELECT * FROM titles WHERE tmdb_id = ? AND media_type = ?",
        (tmdb_id, media_type),
    ).fetchone()
    normalized_incoming_imdb = normalize_imdb_title_id_fn(imdb_id)

    if existing:
        final_imdb = normalized_incoming_imdb or normalize_imdb_title_id_fn(existing["imdb_id"])
        final_last_mdblist = now_ts

        conn.execute(
            """
            UPDATE titles
            SET title = ?,
                imdb_id = ?,
                popularity = ?,
                tmdb_vote_average = ?,
                last_seen_at = ?,
                last_harvested_at = ?,
                last_mdblist_fetch_at = ?,
                last_error = ?
            WHERE tmdb_id = ? AND media_type = ?
            """,
            (
                title,
                final_imdb,
                popularity,
                tmdb_vote_average,
                now_ts,
                now_ts,
                final_last_mdblist,
                error_message,
                tmdb_id,
                media_type,
            ),
        )
        return

    conn.execute(
        """
        INSERT INTO titles(
            tmdb_id,
            media_type,
            title,
            imdb_id,
            popularity,
            tmdb_vote_average,
            last_seen_at,
            last_harvested_at,
            last_mdblist_fetch_at,
            last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tmdb_id,
            media_type,
            title,
            normalized_incoming_imdb,
            popularity,
            tmdb_vote_average,
            now_ts,
            now_ts,
            now_ts,
            error_message,
        ),
    )


def upsert_rating(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    score: float,
    fetched_at: int,
    score_to_tenths_fn: Callable[[Any], Optional[int]],
) -> bool:
    existing = conn.execute(
        """
        SELECT score, pmdb_status, pmdb_item_id
        FROM ratings
        WHERE tmdb_id = ? AND media_type = ? AND label = ?
        """,
        (tmdb_id, media_type, label),
    ).fetchone()

    if not existing:
        conn.execute(
            """
            INSERT INTO ratings(
                tmdb_id,
                media_type,
                label,
                score,
                fetched_at,
                pmdb_item_id,
                pmdb_status,
                pmdb_claimed_at,
                pmdb_submitted_at,
                pmdb_attempts,
                pmdb_last_error,
                pmdb_retry_after
            ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, 0, NULL, 0)
            """,
            (tmdb_id, media_type, label, score, fetched_at),
        )
        return True

    existing_tenths = score_to_tenths_fn(existing["score"])
    new_tenths = score_to_tenths_fn(score)
    if existing_tenths is None or new_tenths is None:
        score_changed = True
    else:
        score_changed = existing_tenths != new_tenths
    previous_status = str(existing["pmdb_status"])

    if score_changed or previous_status in {"failed", "retry"}:
        conn.execute(
            """
            UPDATE ratings
            SET score = ?,
                fetched_at = ?,
                pmdb_status = 'pending',
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = NULL,
                pmdb_attempts = 0,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (score, fetched_at, tmdb_id, media_type, label),
        )
        return True

    conn.execute(
        """
        UPDATE ratings
        SET fetched_at = ?
        WHERE tmdb_id = ? AND media_type = ? AND label = ?
        """,
        (fetched_at, tmdb_id, media_type, label),
    )
    return False


def upsert_episode_rating(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
    score: float,
    fetched_at: int,
    imdb_parent_id: Optional[str],
    imdb_episode_id: Optional[str],
    votes: Optional[int],
    score_to_tenths_fn: Callable[[Any], Optional[int]],
) -> bool:
    existing = conn.execute(
        """
        SELECT score, pmdb_status, pmdb_item_id
        FROM episode_ratings
        WHERE tmdb_id = ?
          AND media_type = ?
          AND season = ?
          AND episode = ?
          AND label = ?
        """,
        (tmdb_id, media_type, season, episode, label),
    ).fetchone()

    if not existing:
        conn.execute(
            """
            INSERT INTO episode_ratings(
                tmdb_id,
                media_type,
                season,
                episode,
                label,
                score,
                fetched_at,
                imdb_parent_id,
                imdb_episode_id,
                votes,
                pmdb_item_id,
                pmdb_status,
                pmdb_claimed_at,
                pmdb_submitted_at,
                pmdb_attempts,
                pmdb_last_error,
                pmdb_retry_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, 0, NULL, 0)
            """,
            (
                tmdb_id,
                media_type,
                season,
                episode,
                label,
                score,
                fetched_at,
                imdb_parent_id,
                imdb_episode_id,
                votes,
            ),
        )
        return True

    existing_tenths = score_to_tenths_fn(existing["score"])
    new_tenths = score_to_tenths_fn(score)
    if existing_tenths is None or new_tenths is None:
        score_changed = True
    else:
        score_changed = existing_tenths != new_tenths
    previous_status = str(existing["pmdb_status"])

    if score_changed or previous_status in {"failed", "retry"}:
        conn.execute(
            """
            UPDATE episode_ratings
            SET score = ?,
                fetched_at = ?,
                imdb_parent_id = ?,
                imdb_episode_id = ?,
                votes = ?,
                pmdb_status = 'pending',
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = NULL,
                pmdb_attempts = 0,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            (
                score,
                fetched_at,
                imdb_parent_id,
                imdb_episode_id,
                votes,
                tmdb_id,
                media_type,
                season,
                episode,
                label,
            ),
        )
        return True

    conn.execute(
        """
        UPDATE episode_ratings
        SET fetched_at = ?,
            imdb_parent_id = ?,
            imdb_episode_id = ?,
            votes = ?
        WHERE tmdb_id = ?
          AND media_type = ?
          AND season = ?
          AND episode = ?
          AND label = ?
        """,
        (
            fetched_at,
            imdb_parent_id,
            imdb_episode_id,
            votes,
            tmdb_id,
            media_type,
            season,
            episode,
            label,
        ),
    )
    return False


def upsert_mapping(
    conn: sqlite3.Connection,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    id_value: str,
    fetched_at: int,
    normalize_imdb_title_id_fn: Callable[[Any], Optional[str]],
) -> bool:
    if id_type == "imdb":
        normalized_imdb = normalize_imdb_title_id_fn(id_value)
        if not normalized_imdb:
            return False
        id_value = normalized_imdb

    existing = conn.execute(
        """
        SELECT id_value, pmdb_status, pmdb_item_id, pmdb_item_value
        FROM mappings
        WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
        """,
        (tmdb_id, media_type, id_type),
    ).fetchone()

    if not existing:
        conn.execute(
            """
            INSERT INTO mappings(
                tmdb_id,
                media_type,
                id_type,
                id_value,
                fetched_at,
                pmdb_item_id,
                pmdb_status,
                pmdb_claimed_at,
                pmdb_submitted_at,
                pmdb_attempts,
                pmdb_last_error,
                pmdb_retry_after
            ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, 0, NULL, 0)
            """,
            (tmdb_id, media_type, id_type, id_value, fetched_at),
        )
        return True

    value_changed = str(existing["id_value"]) != id_value
    previous_status = str(existing["pmdb_status"])
    needs_legacy_validation = (
        previous_status == "submitted"
        and bool(existing["pmdb_item_id"])
        and not bool(existing["pmdb_item_value"])
    )

    if value_changed or previous_status in {"failed", "retry"} or needs_legacy_validation:
        conn.execute(
            """
            UPDATE mappings
            SET id_value = ?,
                fetched_at = ?,
                pmdb_status = 'pending',
                pmdb_claimed_at = NULL,
                pmdb_submitted_at = NULL,
                pmdb_attempts = 0,
                pmdb_last_error = NULL,
                pmdb_retry_after = 0
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (id_value, fetched_at, tmdb_id, media_type, id_type),
        )
        return True

    conn.execute(
        """
        UPDATE mappings
        SET fetched_at = ?
        WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
        """,
        (fetched_at, tmdb_id, media_type, id_type),
    )
    return False
