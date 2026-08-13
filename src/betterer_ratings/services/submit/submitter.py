from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Sequence

import betterer_ratings.constants as package_constants
from betterer_ratings.config.schema import AppConfig
from betterer_ratings.core.clock import now_epoch
from betterer_ratings.core.parsing import first_non_empty, parse_int
from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.domain import models as domain_models
from betterer_ratings.infra.db.local_database import LocalDatabase
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.services.submit import maintenance as submit_maintenance
from betterer_ratings.services.submit import retry_policy as submit_retry_policy
from betterer_ratings.services.submit import setup as submit_setup
from betterer_ratings.services.submit.handler_episode_batch import submit_episode_ratings_batch
from betterer_ratings.services.submit.handler_mapping import submit_mapping, submit_mapping_group
from betterer_ratings.services.submit.handler_rating import submit_rating
from betterer_ratings.services.submit.lease_recovery import lease_recovery_loop
from betterer_ratings.services.submit.runner import run_submitter
from betterer_ratings.services.submit.worker import queue_order_for_worker, worker_loop

LOGGER = logging.getLogger("betterer-ratings")

PMDBSubmitResult = domain_models.PMDBSubmitResult
RETRY_STORM_CLEANUP_KEY = package_constants.RETRY_STORM_CLEANUP_KEY


class Submitter:
    def __init__(
        self,
        *,
        config: AppConfig,
        db: LocalDatabase,
        pmdb_client: PMDBClient,
    ):
        self.config = config
        self.db = db
        self.pmdb_client = pmdb_client
        self.poll_seconds: float = 0.0
        self.worker_count: int = 1
        self.in_flight_lease_seconds: int = 30
        self.max_retry_attempts: int = 1
        self.lease_recovery_interval: float = 10.0
        self._verify_after_transient_statuses: set[int] = set()
        submit_setup.configure_submitter(
            submitter=self,
            config=config,
        )

    @staticmethod
    def _format_pmdb_error(
        *,
        endpoint_hint: str,
        result: PMDBSubmitResult,
    ) -> str:
        return submit_retry_policy.format_pmdb_error(
            endpoint_hint=endpoint_hint,
            result=result,
        )

    @staticmethod
    def _format_manual_error(
        *,
        endpoint: str,
        status: int,
        code: str,
        retryable: bool,
        message: str,
    ) -> str:
        return submit_retry_policy.format_manual_error(
            endpoint=endpoint,
            status=status,
            code=code,
            retryable=retryable,
            message=message,
        )

    def _retry_delay_seconds(self, result: PMDBSubmitResult, current_attempts: int) -> int:
        return submit_retry_policy.retry_delay_seconds(
            retry_after_seconds=int(result.retry_after_seconds or 30),
            current_attempts=current_attempts,
        )

    def _run_one_time_retry_storm_cleanup(self) -> None:
        submit_maintenance.run_one_time_retry_storm_cleanup(
            db=self.db,
            max_retry_attempts=self.max_retry_attempts,
            retry_storm_cleanup_key=RETRY_STORM_CLEANUP_KEY,
            now_epoch_fn=now_epoch,
            logger=LOGGER,
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        await run_submitter(
            stop_event=stop_event,
            run_one_time_retry_storm_cleanup_fn=self._run_one_time_retry_storm_cleanup,
            db=self.db,
            worker_count=self.worker_count,
            worker_loop_fn=self._worker_loop,
            lease_recovery_loop_fn=self._lease_recovery_loop,
            logger=LOGGER,
        )

    async def _lease_recovery_loop(self, stop_event: asyncio.Event) -> None:
        await lease_recovery_loop(
            stop_event=stop_event,
            db=self.db,
            lease_recovery_interval=self.lease_recovery_interval,
            in_flight_lease_seconds=self.in_flight_lease_seconds,
            logger=LOGGER,
        )

    async def _worker_loop(self, stop_event: asyncio.Event, worker_id: int) -> None:
        await worker_loop(
            stop_event=stop_event,
            db=self.db,
            poll_seconds=self.poll_seconds,
            now_epoch_fn=now_epoch,
            submit_mapping_group_fn=self._submit_mapping_group,
            submit_rating_fn=self._submit_rating,
            submit_episode_ratings_batch_fn=self._submit_episode_ratings_batch,
            episode_batch_size=50,
            queue_order=queue_order_for_worker(worker_id, self.worker_count),
        )

    async def _submit_mapping_group(self, rows: Sequence[sqlite3.Row]) -> None:
        await submit_mapping_group(
            rows=rows,
            pmdb_client=self.pmdb_client,
            db=self.db,
            submit_mapping_fn=self._submit_mapping,
            now_epoch_fn=now_epoch,
            logger=LOGGER,
        )

    async def _submit_mapping(self, row: sqlite3.Row) -> None:
        await submit_mapping(
            row=row,
            pmdb_client=self.pmdb_client,
            db=self.db,
            verify_after_transient_statuses=self._verify_after_transient_statuses,
            max_retry_attempts=self.max_retry_attempts,
            format_manual_error_fn=self._format_manual_error,
            format_pmdb_error_fn=self._format_pmdb_error,
            retry_delay_seconds_fn=self._retry_delay_seconds,
            now_epoch_fn=now_epoch,
            first_non_empty_fn=first_non_empty,
            logger=LOGGER,
        )

    async def _submit_rating(self, row: sqlite3.Row) -> None:
        await submit_rating(
            row=row,
            pmdb_client=self.pmdb_client,
            db=self.db,
            verify_after_transient_statuses=self._verify_after_transient_statuses,
            max_retry_attempts=self.max_retry_attempts,
            format_manual_error_fn=self._format_manual_error,
            format_pmdb_error_fn=self._format_pmdb_error,
            retry_delay_seconds_fn=self._retry_delay_seconds,
            now_epoch_fn=now_epoch,
            first_non_empty_fn=first_non_empty,
            logger=LOGGER,
        )

    async def _submit_episode_ratings_batch(self, rows: Sequence[sqlite3.Row]) -> None:
        await submit_episode_ratings_batch(
            rows=rows,
            pmdb_client=self.pmdb_client,
            db=self.db,
            max_retry_attempts=self.max_retry_attempts,
            format_manual_error_fn=self._format_manual_error,
            retry_delay_seconds_fn=self._retry_delay_seconds,
            now_epoch_fn=now_epoch,
            first_non_empty_fn=first_non_empty,
            parse_int_fn=parse_int,
            parse_retry_after_fn=parse_retry_after,
            pmdb_submit_result_cls=PMDBSubmitResult,
            extract_error_code_fn=PMDBClient._extract_error_code,
            is_cloudflare_challenge_fn=PMDBClient._is_cloudflare_challenge,
            logger=LOGGER,
        )
