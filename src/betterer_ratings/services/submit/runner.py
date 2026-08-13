from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping

from betterer_ratings.core.clock import now_epoch

QUEUE_STALL_ALERT_SECONDS = 300
QUEUE_STALL_REPEAT_SECONDS = 900
QUEUE_FAILED_REPEAT_SECONDS = 3600


@dataclass
class QueueAlertMonitor:
    stall_threshold_seconds: int = QUEUE_STALL_ALERT_SECONDS
    stall_repeat_seconds: int = QUEUE_STALL_REPEAT_SECONDS
    failed_repeat_seconds: int = QUEUE_FAILED_REPEAT_SECONDS
    last_progress_at: int | None = None
    last_day: str | None = None
    last_submitted: tuple[int, int, int] | None = None
    last_stall_alert_at: int = 0
    stall_alert_active: bool = False
    last_failed_counts: tuple[int, int, int] = field(default_factory=lambda: (0, 0, 0))
    last_failed_alert_at: int = 0

    def observe(
        self,
        *,
        now_ts: int,
        counts: Mapping[str, int],
        due_counts: Mapping[str, int],
        summary: Mapping[str, int | str],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        submitted = (
            int(summary["ratings_today"]),
            int(summary["mappings_today"]),
            int(summary["episode_ratings_today"]),
        )
        day = str(summary["day"])

        progressed = False
        if self.last_progress_at is None:
            self.last_progress_at = int(now_ts)
        elif day != self.last_day or submitted != self.last_submitted:
            progressed = True
            stalled_seconds = max(0, int(now_ts) - int(self.last_progress_at))
            self.last_progress_at = int(now_ts)
            if self.stall_alert_active:
                events.append(
                    {
                        "event": "queue.alert.recovered",
                        "alert_type": "throughput_stalled",
                        "stalled_seconds": stalled_seconds,
                    }
                )
            self.stall_alert_active = False
            self.last_stall_alert_at = 0

        self.last_day = day
        self.last_submitted = submitted

        failed_counts = (
            int(counts["ratings_failed"]),
            int(counts["mappings_failed"]),
            int(counts["episode_ratings_failed"]),
        )
        failed_total = sum(failed_counts)
        failed_changed = failed_counts != self.last_failed_counts
        if failed_total > 0 and (
            failed_changed
            or self.last_failed_alert_at <= 0
            or int(now_ts) - self.last_failed_alert_at >= self.failed_repeat_seconds
        ):
            events.append(
                {
                    "event": "queue.alert.failed",
                    "alert_type": "failed_rows",
                    "failed_total": failed_total,
                    "ratings_failed": failed_counts[0],
                    "mappings_failed": failed_counts[1],
                    "episode_ratings_failed": failed_counts[2],
                }
            )
            self.last_failed_alert_at = int(now_ts)
        elif failed_total == 0:
            self.last_failed_alert_at = 0
        self.last_failed_counts = failed_counts

        if not _queue_has_active_work(counts, due_counts):
            self.last_progress_at = int(now_ts)
            self.stall_alert_active = False
            self.last_stall_alert_at = 0
            return events

        stalled_seconds = max(0, int(now_ts) - int(self.last_progress_at or now_ts))
        if (
            not progressed
            and stalled_seconds >= self.stall_threshold_seconds
            and (
                self.last_stall_alert_at <= 0
                or int(now_ts) - self.last_stall_alert_at >= self.stall_repeat_seconds
            )
        ):
            events.append(
                {
                    "event": "queue.alert.stalled",
                    "alert_type": "throughput_stalled",
                    "stalled_seconds": stalled_seconds,
                    **{key: int(value) for key, value in counts.items()},
                }
            )
            self.last_stall_alert_at = int(now_ts)
            self.stall_alert_active = True

        return events


def format_queue_status(counts: Mapping[str, int]) -> str:
    return (
        "ratings(pending={ratings_pending} in_flight={ratings_in_flight} failed={ratings_failed}) "
        "mappings(pending={mappings_pending} in_flight={mappings_in_flight} failed={mappings_failed}) "
        "episode_ratings(pending={episode_ratings_pending} in_flight={episode_ratings_in_flight} failed={episode_ratings_failed})"
    ).format(**counts)


def format_submission_summary(summary: Mapping[str, int | str]) -> str:
    return (
        "day={day} submitted_today(ratings={ratings_today} mappings={mappings_today} "
        "episode_ratings={episode_ratings_today}) db_total(titles={titles_total} "
        "ratings={ratings_total} mappings={mappings_total} episode_ratings={episode_ratings_total})"
    ).format(**summary)


def submission_log_extra(summary: Mapping[str, int | str]) -> dict[str, int | str]:
    return {
        "event": "submission.summary",
        **{
            key: value
            for key, value in summary.items()
            if key not in {"day_start_ts", "day_end_ts"}
        },
    }


def _queue_snapshot(counts: Mapping[str, int]) -> tuple[int, ...]:
    return (
        int(counts["ratings_pending"]),
        int(counts["ratings_in_flight"]),
        int(counts["ratings_failed"]),
        int(counts["mappings_pending"]),
        int(counts["mappings_in_flight"]),
        int(counts["mappings_failed"]),
        int(counts["episode_ratings_pending"]),
        int(counts["episode_ratings_in_flight"]),
        int(counts["episode_ratings_failed"]),
    )


def _submission_snapshot(summary: Mapping[str, int | str]) -> tuple[int | str, ...]:
    return (
        summary["day"],
        int(summary["ratings_today"]),
        int(summary["mappings_today"]),
        int(summary["episode_ratings_today"]),
        int(summary["titles_total"]),
        int(summary["ratings_total"]),
        int(summary["mappings_total"]),
        int(summary["episode_ratings_total"]),
    )


def _queue_has_active_work(
    counts: Mapping[str, int],
    due_counts: Mapping[str, int],
) -> bool:
    return any(
        int(counts[key]) > 0
        for key in (
            "ratings_in_flight",
            "mappings_in_flight",
            "episode_ratings_in_flight",
        )
    ) or any(
        int(due_counts[key]) > 0
        for key in (
            "ratings_due",
            "mappings_due",
            "episode_ratings_due",
        )
    )


async def queue_progress_loop(
    *,
    stop_event: asyncio.Event,
    db: Any,
    logger: Any,
    interval_seconds: int = 5,
    now_epoch_fn: Any = now_epoch,
    alert_monitor: QueueAlertMonitor | None = None,
) -> None:
    last_snapshot: tuple[int, ...] | None = None
    last_submission_snapshot: tuple[int | str, ...] | None = None
    last_submission_log_ts = 0
    monitor = alert_monitor or QueueAlertMonitor()
    while not stop_event.is_set():
        now_ts = int(now_epoch_fn())
        counts = db.queue_counts()
        due_counts = {
            "ratings_due": db.count_due_queue(kind="rating", now_ts=now_ts),
            "mappings_due": db.count_due_queue(kind="mapping", now_ts=now_ts),
            "episode_ratings_due": db.count_due_queue(
                kind="episode_ratings",
                now_ts=now_ts,
            ),
        }
        snapshot = _queue_snapshot(counts)
        has_active_work = _queue_has_active_work(counts, due_counts)
        if snapshot != last_snapshot or has_active_work:
            logger.info(
                "[Submitter] Queue status: %s.",
                format_queue_status(counts),
                extra={"event": "queue.status", **counts},
            )
            last_snapshot = snapshot
        summary = db.submission_summary(now_ts)
        for alert in monitor.observe(
            now_ts=now_ts,
            counts=counts,
            due_counts=due_counts,
            summary=summary,
        ):
            event = str(alert["event"])
            if event == "queue.alert.failed":
                logger.warning(
                    "[Submitter] Queue alert: failed rows detected "
                    "(ratings=%s mappings=%s episode_ratings=%s total=%s).",
                    alert["ratings_failed"],
                    alert["mappings_failed"],
                    alert["episode_ratings_failed"],
                    alert["failed_total"],
                    extra=alert,
                )
            elif event == "queue.alert.stalled":
                logger.warning(
                    "[Submitter] Queue alert: no successful submissions for %ss while work is active. %s.",
                    alert["stalled_seconds"],
                    format_queue_status(counts),
                    extra=alert,
                )
            elif event == "queue.alert.recovered":
                logger.info(
                    "[Submitter] Queue throughput recovered after a %ss stall.",
                    alert["stalled_seconds"],
                    extra=alert,
                )
        submission_snapshot = _submission_snapshot(summary)
        if (
            submission_snapshot != last_submission_snapshot
            or last_submission_log_ts <= 0
            or now_ts - last_submission_log_ts >= 3600
        ):
            logger.info(
                "[Submitter] Submission summary: %s.",
                format_submission_summary(summary),
                extra=submission_log_extra(summary),
            )
            last_submission_snapshot = submission_snapshot
            last_submission_log_ts = now_ts
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(1, int(interval_seconds)))
        except asyncio.TimeoutError:
            pass


async def run_submitter(
    *,
    stop_event: asyncio.Event,
    run_one_time_retry_storm_cleanup_fn: Any,
    db: Any,
    worker_count: int,
    worker_loop_fn: Any,
    lease_recovery_loop_fn: Any,
    logger: Any,
) -> None:
    run_one_time_retry_storm_cleanup_fn()
    db.recover_in_flight_rows()
    logger.info("[Submitter] Startup recovery: moved stale in_flight rows to retry.")
    logger.info("[Submitter] Starting %s worker(s).", worker_count)

    workers = [asyncio.create_task(worker_loop_fn(stop_event, i + 1)) for i in range(worker_count)]
    lease_recovery_task = asyncio.create_task(
        lease_recovery_loop_fn(stop_event),
        name="submitter_lease_recovery",
    )
    queue_progress_task = asyncio.create_task(
        queue_progress_loop(stop_event=stop_event, db=db, logger=logger),
        name="submitter_queue_progress",
    )
    stop_waiter = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        [*workers, lease_recovery_task, queue_progress_task, stop_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )

    worker_errors = []
    for task in done:
        if task is stop_waiter:
            continue
        exc = task.exception()
        if exc is not None:
            worker_errors.append(exc)

    if worker_errors:
        stop_event.set()

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # Ensure claimed rows are not stranded after cancellation/shutdown.
    db.recover_in_flight_rows()
    logger.info("[Submitter] Shutdown recovery: moved in_flight rows to retry.")

    if worker_errors:
        raise worker_errors[0]

    logger.info("[Submitter] Stopped.")
