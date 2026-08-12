from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Dict, Optional

import httpx

from betterer_ratings.core.clock import format_duration, now_epoch
from betterer_ratings.core.network import is_network_unavailable_error
from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.core.urls import sanitize_url_for_logs
from betterer_ratings.domain.models import APIResponse


class HTTPClient:
    def __init__(
        self,
        timeout_seconds: int,
        max_retries: int,
        *,
        now_epoch_fn: Callable[[], int] = now_epoch,
        parse_retry_after_fn: Callable[[Optional[str], int], int] = parse_retry_after,
        sanitize_url_for_logs_fn: Callable[[str], str] = sanitize_url_for_logs,
        is_network_unavailable_error_fn: Callable[[Exception], bool] = is_network_unavailable_error,
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._network_error_throttle_seconds = 20
        self._last_network_error_log_at: Dict[str, int] = {}

        self._now_epoch = now_epoch_fn
        self._parse_retry_after = parse_retry_after_fn
        self._sanitize_url_for_logs = sanitize_url_for_logs_fn
        self._is_network_unavailable_error = is_network_unavailable_error_fn
        self._logger = logger or logging.getLogger("betterer-ratings")
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=self.timeout_seconds)
        )
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    def _should_log_network_error(self, key: str) -> bool:
        now_ts = self._now_epoch()
        last = self._last_network_error_log_at.get(key, 0)
        if now_ts - last >= self._network_error_throttle_seconds:
            self._last_network_error_log_at[key] = now_ts
            return True
        return False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    async def _recycle_client(self, expected_client: httpx.AsyncClient) -> bool:
        """Replace a suspect shared pool once, even when many requests fail together."""
        async with self._client_lock:
            if self._client is not expected_client:
                return False
            self._client = self._client_factory()
        await expected_client.aclose()
        return True

    async def aclose(self) -> None:
        async with self._client_lock:
            if self._client is None:
                return
            await self._client.aclose()
            self._client = None

    async def request_json(
        self,
        *,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        gate: Optional[Any] = None,
        max_pause_wait_seconds: Optional[int] = None,
    ) -> APIResponse:
        last_response: Optional[APIResponse] = None
        safe_url = self._sanitize_url_for_logs(url)
        consecutive_pool_errors = 0

        for attempt in range(self.max_retries):
            if gate is not None:
                acquired = await gate.acquire(max_pause_wait_seconds=max_pause_wait_seconds)
                if not acquired:
                    return APIResponse(
                        status=429,
                        headers={},
                        data={"error": "service paused"},
                        text="service paused",
                    )

            try:
                client = await self._get_client()
                raw_resp = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                sleep_for = min(60, (2**attempt) + random.random())
                error_type = exc.__class__.__name__
                error_repr = repr(exc)
                recyclable_pool_error = isinstance(
                    exc,
                    (httpx.ReadError, httpx.RemoteProtocolError),
                )
                consecutive_pool_errors = (
                    consecutive_pool_errors + 1 if recyclable_pool_error else 0
                )
                pool_recycled = False
                if consecutive_pool_errors >= 2 and attempt < self.max_retries - 1:
                    pool_recycled = await self._recycle_client(client)
                    consecutive_pool_errors = 0
                    if pool_recycled:
                        self._logger.info(
                            "HTTP connection pool recycled after repeated transport failures: "
                            "%s %s error_type=%s error_repr=%s",
                            method,
                            safe_url,
                            error_type,
                            error_repr,
                            extra={
                                "event": "http.connection_pool_recycled",
                                "method": method,
                                "endpoint": safe_url,
                                "error_type": error_type,
                                "error_repr": error_repr,
                                "attempt": attempt + 1,
                            },
                        )
                if self._is_network_unavailable_error(exc):
                    pause_for = int(max(5, min(60, sleep_for)))
                    if gate is not None:
                        gate.pause_for(pause_for, "Network unavailable")
                    gate_name = gate.name if gate is not None else "http"
                    throttle_key = f"{gate_name}:{method}:{safe_url}:{exc.__class__.__name__}"
                    if self._should_log_network_error(throttle_key):
                        self._logger.info(
                            "Network unavailable for %s %s (attempt %s/%s). "
                            "Backing off %.1fs: error_type=%s error_repr=%s",
                            method,
                            safe_url,
                            attempt + 1,
                            self.max_retries,
                            sleep_for,
                            error_type,
                            error_repr,
                            extra={
                                "event": "http.transport_retry",
                                "method": method,
                                "endpoint": safe_url,
                                "attempt": attempt + 1,
                                "max_attempts": self.max_retries,
                                "backoff_seconds": sleep_for,
                                "error_type": error_type,
                                "error_repr": error_repr,
                                "pool_recycled": pool_recycled,
                            },
                        )
                else:
                    self._logger.warning(
                        "HTTP error calling %s %s (attempt %s/%s): "
                        "error_type=%s error_repr=%s",
                        method,
                        safe_url,
                        attempt + 1,
                        self.max_retries,
                        error_type,
                        error_repr,
                        extra={
                            "event": "http.error",
                            "method": method,
                            "endpoint": safe_url,
                            "attempt": attempt + 1,
                            "max_attempts": self.max_retries,
                            "error_type": error_type,
                            "error_repr": error_repr,
                            "pool_recycled": pool_recycled,
                        },
                    )

                if attempt == self.max_retries - 1:
                    return APIResponse(
                        status=0,
                        headers={},
                        data={"error": str(exc)},
                        text=str(exc),
                    )
                await asyncio.sleep(sleep_for)
                continue

            consecutive_pool_errors = 0

            normalized_headers = {str(k).lower(): str(v) for k, v in raw_resp.headers.items()}
            data: Any = None
            text = raw_resp.text or ""
            if text:
                try:
                    data = raw_resp.json()
                except ValueError:
                    data = None

            response = APIResponse(
                status=raw_resp.status_code,
                headers=normalized_headers,
                data=data,
                text=text,
            )
            last_response = response

            if gate is not None:
                gate.observe_headers(response.headers, response.status)

            if response.status == 429:
                retry_after = self._parse_retry_after(response.headers.get("retry-after"), 5)
                if gate is not None:
                    gate.pause_for(retry_after, "429 Too Many Requests")
                self._logger.warning(
                    "429 from %s %s. Retry-After=%s",
                    method,
                    safe_url,
                    format_duration(retry_after),
                )
                if attempt == self.max_retries - 1:
                    return response
                await asyncio.sleep(retry_after)
                continue

            if response.status in {500, 502, 503, 504}:
                sleep_for = min(60, (2**attempt) + random.random())
                self._logger.warning(
                    "%s from %s %s. Retrying in %.1fs (attempt %s/%s)",
                    response.status,
                    method,
                    safe_url,
                    sleep_for,
                    attempt + 1,
                    self.max_retries,
                )
                if attempt == self.max_retries - 1:
                    return response
                await asyncio.sleep(sleep_for)
                continue

            if response.status in {401, 403}:
                throttle_key = f"auth:{method}:{safe_url}:{response.status}"
                if self._should_log_network_error(throttle_key):
                    compact = " ".join(text.split())[:180]
                    suffix = f" body={compact}" if compact else ""
                    self._logger.warning(
                        "%s from %s %s%s",
                        response.status,
                        method,
                        safe_url,
                        suffix,
                    )

            return response

        if last_response is not None:
            return last_response

        return APIResponse(status=0, headers={}, data=None, text="unknown error")
