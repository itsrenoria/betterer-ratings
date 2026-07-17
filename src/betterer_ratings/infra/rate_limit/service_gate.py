from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from betterer_ratings.core.clock import format_duration, now_epoch, to_iso
from betterer_ratings.core.parsing import parse_int


class ServiceGate:
    """Combines local window throttling and persisted pause state."""

    def __init__(
        self,
        name: str,
        db: Any,
        limiter: Any,
        *,
        daily_reserve: int = 0,
        now_epoch_fn: Callable[[], int] = now_epoch,
        parse_int_fn: Callable[[Any], Optional[int]] = parse_int,
        to_iso_fn: Callable[[int], str] = to_iso,
        logger: Optional[logging.Logger] = None,
    ):
        self.name = name
        self.db = db
        self.limiter = limiter
        self.daily_reserve = max(0, int(daily_reserve))
        self._now_epoch = now_epoch_fn
        self._parse_int = parse_int_fn
        self._to_iso = to_iso_fn
        self._logger = logger or logging.getLogger("betterer-ratings")

        self.paused_until = 0
        self.pause_reason = ""
        self.rate_limit = None
        self.rate_remaining = None
        self.rate_reset = None
        self.last_status = None
        self._last_pause_log = 0
        self._last_state_persist_at = 0
        self._state_dirty = False
        self._state_persist_interval_seconds = 5

        existing = self.db.get_service_state(self.name)
        if existing:
            self.paused_until = int(existing["paused_until"] or 0)
            self.pause_reason = str(existing["pause_reason"] or "")
            self.rate_limit = existing["rate_limit"]
            self.rate_remaining = existing["rate_remaining"]
            self.rate_reset = existing["rate_reset"]
            self.last_status = existing["last_status"]

    def _persist_state_if_due(self, *, force: bool = False) -> None:
        if not self._state_dirty and not force:
            return
        now_ts = self._now_epoch()
        if (
            not force
            and self._last_state_persist_at > 0
            and now_ts - self._last_state_persist_at < self._state_persist_interval_seconds
        ):
            return
        self.db.update_service_state(
            service=self.name,
            paused_until=int(self.paused_until),
            pause_reason=str(self.pause_reason or ""),
            rate_limit=self.rate_limit,
            rate_remaining=self.rate_remaining,
            rate_reset=self.rate_reset,
            last_status=self.last_status,
        )
        self._last_state_persist_at = now_ts
        self._state_dirty = False

    def pause_remaining(self) -> int:
        return max(0, int(self.paused_until - self._now_epoch()))

    def pause_for(self, seconds: int, reason: str) -> None:
        until = self._now_epoch() + max(1, int(seconds))
        self.paused_until = max(self.paused_until, until)
        self.pause_reason = reason
        self._state_dirty = True
        self._persist_state_if_due(force=True)

    def pause_until_timestamp(self, epoch_seconds: int, reason: str) -> None:
        if epoch_seconds <= self._now_epoch():
            return
        self.paused_until = max(self.paused_until, epoch_seconds)
        self.pause_reason = reason
        self._state_dirty = True
        self._persist_state_if_due(force=True)

    def observe_headers(self, headers: Dict[str, str], status: int) -> None:
        limit = self._parse_int(headers.get("x-ratelimit-limit"))
        remaining = self._parse_int(headers.get("x-ratelimit-remaining"))
        reset = self._parse_int(headers.get("x-ratelimit-reset"))
        next_limit = limit if limit is not None else self.rate_limit
        next_remaining = remaining if remaining is not None else self.rate_remaining
        next_reset = reset if reset is not None else self.rate_reset
        if (
            next_limit != self.rate_limit
            or next_remaining != self.rate_remaining
            or next_reset != self.rate_reset
            or status != self.last_status
        ):
            self.rate_limit = next_limit
            self.rate_remaining = next_remaining
            self.rate_reset = next_reset
            self.last_status = status
            self._state_dirty = True
            self._persist_state_if_due(force=(status >= 400))
            if self.name == "mdblist" and (self.rate_limit is not None or self.rate_remaining is not None):
                self._logger.info(
                    "[MDBList] Quota updated: remaining=%s/%s reset=%s status=%s.",
                    self.rate_remaining if self.rate_remaining is not None else "unknown",
                    self.rate_limit if self.rate_limit is not None else "unknown",
                    self._to_iso(int(self.rate_reset)) if self.rate_reset else "unknown",
                    status,
                    extra={
                        "event": "quota.updated",
                        "mdblist_rate_limit": self.rate_limit,
                        "mdblist_rate_remaining": self.rate_remaining,
                        "mdblist_rate_reset_at": self._to_iso(int(self.rate_reset))
                        if self.rate_reset
                        else "",
                        "status": status,
                    },
                )

        # Pause once the provider's shared daily quota is drained down to our
        # reserve floor, so other consumers of the same API key (e.g. the user's
        # own watchlist/history syncs) keep the reserved headroom. With
        # daily_reserve=0 this preserves the original behaviour (pause at 0).
        if remaining is not None and remaining <= self.daily_reserve:
            now_ts = self._now_epoch()
            reset_ts = (
                next_reset if next_reset is not None and next_reset > now_ts else now_ts + 300
            )
            reason = (
                "Daily Limit Reached"
                if self.daily_reserve <= 0
                else f"Daily reserve floor reached ({remaining} left, reserve {self.daily_reserve})"
            )
            self.pause_until_timestamp(reset_ts, reason)
            self._logger.warning(
                "[%s] %s; paused until %s.",
                self.name,
                reason,
                self._to_iso(self.paused_until),
            )

    async def acquire(self, max_pause_wait_seconds: Optional[int] = None) -> bool:
        self._persist_state_if_due(force=False)
        while True:
            remaining = self.pause_remaining()
            if remaining <= 0:
                break

            if max_pause_wait_seconds is not None and remaining > max_pause_wait_seconds:
                return False

            now_ts = self._now_epoch()
            if now_ts - self._last_pause_log >= 30:
                reason = self.pause_reason or "Rate limit pause"
                self._logger.warning(
                    "[%s] Paused for %s (%s).",
                    self.name,
                    format_duration(remaining),
                    reason,
                )
                self._last_pause_log = now_ts
            await asyncio.sleep(min(5, remaining))

        await self.limiter.acquire()
        return True
