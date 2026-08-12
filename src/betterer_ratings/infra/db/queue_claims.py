from __future__ import annotations

import sqlite3
from typing import List, Optional, cast


def claim_next_pending_rating(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
) -> Optional[sqlite3.Row]:
    with conn:
        row = conn.execute(
            """
            WITH next_pending AS (
                SELECT tmdb_id, media_type, label, score, pmdb_item_id, pmdb_attempts, fetched_at
                FROM ratings INDEXED BY idx_ratings_claim_order
                WHERE pmdb_status = 'pending'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC
                LIMIT 1
            ),
            next_retry AS (
                SELECT tmdb_id, media_type, label, score, pmdb_item_id, pmdb_attempts, fetched_at
                FROM ratings INDEXED BY idx_ratings_claim_order
                WHERE pmdb_status = 'retry'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC
                LIMIT 1
            ),
            next_row AS (
                SELECT * FROM next_pending
                UNION ALL
                SELECT * FROM next_retry
            )
            SELECT tmdb_id, media_type, label, score, pmdb_item_id, pmdb_attempts
            FROM next_row
            ORDER BY fetched_at ASC
            LIMIT 1
            """,
            (int(now_ts), int(now_ts)),
        ).fetchone()

        if row is None:
            return None

        conn.execute(
            """
            UPDATE ratings
            SET pmdb_status = 'in_flight',
                pmdb_claimed_at = ?
            WHERE tmdb_id = ? AND media_type = ? AND label = ?
            """,
            (now_ts, row["tmdb_id"], row["media_type"], row["label"]),
        )
        return cast(sqlite3.Row, row)


def claim_next_pending_mapping(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
) -> Optional[sqlite3.Row]:
    with conn:
        row = conn.execute(
            """
            WITH next_pending AS (
                SELECT tmdb_id, media_type, id_type, id_value, pmdb_item_id, pmdb_attempts, fetched_at
                FROM mappings INDEXED BY idx_mappings_claim_order
                WHERE pmdb_status = 'pending'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC
                LIMIT 1
            ),
            next_retry AS (
                SELECT tmdb_id, media_type, id_type, id_value, pmdb_item_id, pmdb_attempts, fetched_at
                FROM mappings INDEXED BY idx_mappings_claim_order
                WHERE pmdb_status = 'retry'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC
                LIMIT 1
            ),
            next_row AS (
                SELECT * FROM next_pending
                UNION ALL
                SELECT * FROM next_retry
            )
            SELECT tmdb_id, media_type, id_type, id_value, pmdb_item_id, pmdb_attempts
            FROM next_row
            ORDER BY fetched_at ASC
            LIMIT 1
            """,
            (int(now_ts), int(now_ts)),
        ).fetchone()

        if row is None:
            return None

        conn.execute(
            """
            UPDATE mappings
            SET pmdb_status = 'in_flight',
                pmdb_claimed_at = ?
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            (now_ts, row["tmdb_id"], row["media_type"], row["id_type"]),
        )
        return cast(sqlite3.Row, row)


def claim_next_pending_mapping_group(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
) -> List[sqlite3.Row]:
    """Claim all due mappings for the oldest title in one transaction."""
    with conn:
        first_row = conn.execute(
            """
            WITH next_pending AS (
                SELECT tmdb_id, media_type, fetched_at, rowid AS row_id
                FROM mappings INDEXED BY idx_mappings_claim_order
                WHERE pmdb_status = 'pending'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC, rowid ASC
                LIMIT 1
            ),
            next_retry AS (
                SELECT tmdb_id, media_type, fetched_at, rowid AS row_id
                FROM mappings INDEXED BY idx_mappings_claim_order
                WHERE pmdb_status = 'retry'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC, rowid ASC
                LIMIT 1
            ),
            next_row AS (
                SELECT * FROM next_pending
                UNION ALL
                SELECT * FROM next_retry
            )
            SELECT tmdb_id, media_type
            FROM next_row
            ORDER BY fetched_at ASC, row_id ASC
            LIMIT 1
            """,
            (int(now_ts), int(now_ts)),
        ).fetchone()
        if first_row is None:
            return []

        rows = conn.execute(
            """
            SELECT tmdb_id, media_type, id_type, id_value, pmdb_item_id, pmdb_attempts
            FROM mappings
            WHERE pmdb_status IN ('pending', 'retry')
              AND pmdb_retry_after <= ?
              AND tmdb_id = ?
              AND media_type = ?
            ORDER BY fetched_at ASC, rowid ASC
            """,
            (int(now_ts), int(first_row["tmdb_id"]), str(first_row["media_type"])),
        ).fetchall()
        if not rows:
            return []

        conn.executemany(
            """
            UPDATE mappings
            SET pmdb_status = 'in_flight',
                pmdb_claimed_at = ?
            WHERE tmdb_id = ? AND media_type = ? AND id_type = ?
            """,
            [
                (
                    int(now_ts),
                    int(row["tmdb_id"]),
                    str(row["media_type"]),
                    str(row["id_type"]),
                )
                for row in rows
            ],
        )
        return rows


def claim_next_pending_episode_ratings_batch(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    batch_size: int = 50,
) -> List[sqlite3.Row]:
    safe_batch_size = max(1, min(50, int(batch_size)))
    with conn:
        first_row = conn.execute(
            """
            WITH next_pending AS (
                SELECT tmdb_id, media_type, season, fetched_at, rowid AS row_id
                FROM episode_ratings INDEXED BY idx_episode_ratings_claim_order
                WHERE pmdb_status = 'pending'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC, rowid ASC
                LIMIT 1
            ),
            next_retry AS (
                SELECT tmdb_id, media_type, season, fetched_at, rowid AS row_id
                FROM episode_ratings INDEXED BY idx_episode_ratings_claim_order
                WHERE pmdb_status = 'retry'
                  AND pmdb_retry_after <= ?
                ORDER BY fetched_at ASC, rowid ASC
                LIMIT 1
            ),
            next_row AS (
                SELECT * FROM next_pending
                UNION ALL
                SELECT * FROM next_retry
            )
            SELECT tmdb_id, media_type, season
            FROM next_row
            ORDER BY fetched_at ASC, row_id ASC
            LIMIT 1
            """,
            (int(now_ts), int(now_ts)),
        ).fetchone()
        if first_row is None:
            return []

        tmdb_id = int(first_row["tmdb_id"])
        media_type = str(first_row["media_type"])
        season = int(first_row["season"])
        rows = conn.execute(
            """
            SELECT
                tmdb_id,
                media_type,
                season,
                episode,
                label,
                score,
                pmdb_item_id,
                pmdb_attempts
            FROM episode_ratings
            WHERE pmdb_status IN ('pending', 'retry')
              AND pmdb_retry_after <= ?
              AND tmdb_id = ?
              AND media_type = ?
              AND season = ?
            ORDER BY episode ASC, fetched_at ASC, rowid ASC
            LIMIT ?
            """,
            (
                int(now_ts),
                tmdb_id,
                media_type,
                season,
                safe_batch_size,
            ),
        ).fetchall()
        if not rows:
            return []

        conn.executemany(
            """
            UPDATE episode_ratings
            SET pmdb_status = 'in_flight',
                pmdb_claimed_at = ?
            WHERE tmdb_id = ?
              AND media_type = ?
              AND season = ?
              AND episode = ?
              AND label = ?
            """,
            [
                (
                    int(now_ts),
                    int(row["tmdb_id"]),
                    str(row["media_type"]),
                    int(row["season"]),
                    int(row["episode"]),
                    str(row["label"]),
                )
                for row in rows
            ],
        )
        return rows
