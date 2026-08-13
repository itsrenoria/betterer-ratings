from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from betterer_ratings.core.clock import local_day_key, now_epoch
from betterer_ratings.infra.db.queue_claims import (
    claim_next_pending_episode_ratings_batch,
    claim_next_pending_mapping,
    claim_next_pending_mapping_group,
    claim_next_pending_rating,
)
from betterer_ratings.infra.db.queue_metrics import (
    count_due_queue,
    queue_counts,
    submission_summary,
)
from betterer_ratings.infra.db.queue_recovery import (
    cleanup_retry_storm_rows,
    recover_in_flight_rows,
    recover_stale_in_flight_rows,
)
from betterer_ratings.infra.db.queue_retry import (
    clear_rating_pmdb_item_id,
    mark_episode_rating_failed,
    mark_episode_rating_retry,
    mark_mapping_failed,
    mark_mapping_retry,
    mark_rating_failed,
    mark_rating_retry,
)
from betterer_ratings.infra.db.queue_submission import (
    mark_episode_rating_submitted,
    mark_mapping_submitted,
    mark_rating_submitted,
    record_episode_submission_success,
    record_submission_success,
)


class LocalDatabaseQueueMixin:
    conn: sqlite3.Connection

    def recover_in_flight_rows(self) -> None:
        """Re-queue rows that were claimed before an unclean shutdown."""
        recover_in_flight_rows(self.conn)

    def recover_stale_in_flight_rows(self, lease_seconds: int) -> Dict[str, int]:
        return recover_stale_in_flight_rows(
            self.conn,
            lease_seconds=lease_seconds,
            now_epoch_fn=now_epoch,
        )

    def cleanup_retry_storm_rows(self, max_attempts: int) -> Dict[str, int]:
        return cleanup_retry_storm_rows(
            self.conn,
            max_attempts=max_attempts,
        )

    def claim_next_pending_rating(self, now_ts: int) -> Optional[sqlite3.Row]:
        return claim_next_pending_rating(self.conn, now_ts=now_ts)

    def claim_next_pending_mapping(self, now_ts: int) -> Optional[sqlite3.Row]:
        return claim_next_pending_mapping(self.conn, now_ts=now_ts)

    def claim_next_pending_mapping_group(self, now_ts: int) -> List[sqlite3.Row]:
        return claim_next_pending_mapping_group(self.conn, now_ts=now_ts)

    def claim_next_pending_episode_ratings_batch(
        self,
        *,
        now_ts: int,
        batch_size: int = 50,
    ) -> List[sqlite3.Row]:
        return claim_next_pending_episode_ratings_batch(
            self.conn,
            now_ts=now_ts,
            batch_size=batch_size,
        )

    def next_due_queue_kind(self, now_ts: int) -> Optional[str]:
        row = self.conn.execute(
            """
            SELECT kind
            FROM (
                SELECT 'rating' AS kind, MIN(fetched_at) AS fetched_at
                FROM ratings
                WHERE pmdb_status IN ('pending', 'retry')
                  AND pmdb_retry_after <= ?
                UNION ALL
                SELECT 'mapping' AS kind, MIN(fetched_at) AS fetched_at
                FROM mappings
                WHERE pmdb_status IN ('pending', 'retry')
                  AND pmdb_retry_after <= ?
                UNION ALL
                SELECT 'episode_ratings' AS kind, MIN(fetched_at) AS fetched_at
                FROM episode_ratings
                WHERE pmdb_status IN ('pending', 'retry')
                  AND pmdb_retry_after <= ?
            )
            WHERE fetched_at IS NOT NULL
            ORDER BY fetched_at ASC,
                     CASE kind
                         WHEN 'episode_ratings' THEN 0
                         WHEN 'rating' THEN 1
                         ELSE 2
                     END
            LIMIT 1
            """,
            (int(now_ts), int(now_ts), int(now_ts)),
        ).fetchone()
        if row is None:
            return None
        return str(row["kind"])

    def mark_rating_submitted(
        self,
        tmdb_id: int,
        media_type: str,
        label: str,
        submitted_at: int,
        pmdb_item_id: Optional[str] = None,
    ) -> None:
        mark_rating_submitted(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            submitted_at=submitted_at,
            pmdb_item_id=pmdb_item_id,
            local_day_key_fn=local_day_key,
        )

    def mark_mapping_submitted(
        self,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        submitted_at: int,
        pmdb_item_id: Optional[str] = None,
    ) -> None:
        mark_mapping_submitted(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            submitted_at=submitted_at,
            pmdb_item_id=pmdb_item_id,
            local_day_key_fn=local_day_key,
        )

    def mark_episode_rating_submitted(
        self,
        tmdb_id: int,
        media_type: str,
        season: int,
        episode: int,
        label: str,
        submitted_at: int,
        pmdb_item_id: Optional[str] = None,
    ) -> None:
        mark_episode_rating_submitted(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=label,
            submitted_at=submitted_at,
            pmdb_item_id=pmdb_item_id,
            local_day_key_fn=local_day_key,
        )

    def mark_rating_retry(
        self,
        tmdb_id: int,
        media_type: str,
        label: str,
        retry_after: int,
        error_text: str,
    ) -> None:
        mark_rating_retry(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            retry_after=retry_after,
            error_text=error_text,
        )

    def clear_rating_pmdb_item_id(
        self,
        tmdb_id: int,
        media_type: str,
        label: str,
    ) -> None:
        clear_rating_pmdb_item_id(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
        )

    def mark_mapping_retry(
        self,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        retry_after: int,
        error_text: str,
    ) -> None:
        mark_mapping_retry(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            retry_after=retry_after,
            error_text=error_text,
        )

    def mark_episode_rating_retry(
        self,
        tmdb_id: int,
        media_type: str,
        season: int,
        episode: int,
        label: str,
        retry_after: int,
        error_text: str,
    ) -> None:
        mark_episode_rating_retry(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=label,
            retry_after=retry_after,
            error_text=error_text,
        )

    def mark_rating_failed(
        self,
        tmdb_id: int,
        media_type: str,
        label: str,
        error_text: str,
    ) -> None:
        mark_rating_failed(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            error_text=error_text,
        )

    def mark_mapping_failed(
        self,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        error_text: str,
    ) -> None:
        mark_mapping_failed(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            error_text=error_text,
        )

    def mark_episode_rating_failed(
        self,
        tmdb_id: int,
        media_type: str,
        season: int,
        episode: int,
        label: str,
        error_text: str,
    ) -> None:
        mark_episode_rating_failed(
            self.conn,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=label,
            error_text=error_text,
        )

    def queue_counts(self) -> Dict[str, int]:
        return queue_counts(self.conn)

    def submission_summary(self, now_ts: int) -> Dict[str, int | str]:
        return submission_summary(self.conn, now_ts=now_ts)

    def count_due_queue(self, *, kind: str, now_ts: int) -> int:
        return count_due_queue(self.conn, kind=kind, now_ts=now_ts)

    def _record_submission_success(
        self,
        *,
        kind: str,
        tmdb_id: int,
        media_type: str,
        submitted_at: int,
    ) -> None:
        record_submission_success(
            self.conn,
            kind=kind,
            tmdb_id=tmdb_id,
            media_type=media_type,
            submitted_at=submitted_at,
            local_day_key_fn=local_day_key,
        )

    def _record_episode_submission_success(self, *, submitted_at: int) -> None:
        record_episode_submission_success(
            self.conn,
            submitted_at=submitted_at,
            local_day_key_fn=local_day_key,
        )
