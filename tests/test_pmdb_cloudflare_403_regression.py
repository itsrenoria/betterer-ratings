"""Regression coverage for the false-positive Cloudflare-challenge gate pause.

`cf-ray` is present on virtually every Cloudflare-proxied response --
including ordinary, legitimate JSON 403s -- so it must not, on its own,
trigger the 300s PMDB gate pause reserved for genuine Cloudflare
interstitial/challenge pages. These tests exercise the actual call sites
(`pmdb_transport.observe_submission_response` and
`handler_episode_batch.submit_episode_ratings_batch`) rather than just the
classifier helper, so a regression in the wiring is caught too.
"""

from __future__ import annotations

import asyncio

from betterer_ratings.core.parsing import first_non_empty, parse_int
from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.domain.models import APIResponse, PMDBSubmitResult
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.providers.pmdb_transport import observe_submission_response
from betterer_ratings.services.submit.handler_episode_batch import (
    submit_episode_ratings_batch,
)

OWNERSHIP_DENIAL_TEXT = '{"error":"Access denied - you can only delete your own data"}'
OWNERSHIP_DENIAL_DATA = {"error": "Access denied - you can only delete your own data"}


class _NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _FakeGate:
    def __init__(self):
        self.paused: list[tuple[int, str]] = []

    def observe_headers(self, headers, status):
        pass

    def pause_for(self, seconds, reason):
        self.paused.append((seconds, reason))


class _FakeClient:
    def __init__(self):
        self.api_gate = _FakeGate()
        self._logger = _NullLogger()

    _extract_error_code = staticmethod(PMDBClient._extract_error_code)
    _is_cloudflare_challenge = staticmethod(PMDBClient._is_cloudflare_challenge)


def _observe(response: APIResponse):
    client = _FakeClient()
    contribution_gate = _FakeGate()
    observe_submission_response(
        client,
        response,
        contribution_gate,
        method="DELETE",
        endpoint="https://publicmetadb.com/api/external/ratings/1kw6nr54fx01ydo",
    )
    return client.api_gate, contribution_gate


def test_ownership_denial_403_does_not_pause_either_pmdb_gate():
    response = APIResponse(
        status=403,
        headers={
            "content-type": "application/json",
            "cf-ray": "8f1a2b3c4d5e6f7g-SJC",
        },
        data=OWNERSHIP_DENIAL_DATA,
        text=OWNERSHIP_DENIAL_TEXT,
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == []
    assert contribution_gate.paused == []


def test_genuine_cloudflare_challenge_still_pauses_both_gates():
    response = APIResponse(
        status=403,
        headers={"content-type": "text/html; charset=UTF-8"},
        data=None,
        text="Just a moment...",
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == [(300, "Cloudflare challenge")]
    assert contribution_gate.paused == [(300, "Cloudflare challenge")]


def test_cf_mitigated_header_is_the_primary_challenge_signal():
    response = APIResponse(
        status=403,
        headers={
            "content-type": "application/octet-stream",
            "cf-mitigated": "challenge",
        },
        data=None,
        text="",
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == [(300, "Cloudflare challenge")]
    assert contribution_gate.paused == [(300, "Cloudflare challenge")]


def test_ordinary_html_403_does_not_pause_pmdb_gates():
    response = APIResponse(
        status=403,
        headers={
            "content-type": "text/html; charset=UTF-8",
            "cf-ray": "8f1a2b3c4d5e6f7g-SJC",
        },
        data=None,
        text="Access denied",
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == []
    assert contribution_gate.paused == []


def test_attention_required_html_marker_remains_a_challenge_fallback():
    response = APIResponse(
        status=403,
        headers={"content-type": "text/html; charset=UTF-8"},
        data=None,
        text="Attention required! | Cloudflare",
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == [(300, "Cloudflare challenge")]
    assert contribution_gate.paused == [(300, "Cloudflare challenge")]


def test_confirmed_challenge_delete_is_retryable():
    response = APIResponse(
        status=403,
        headers={"cf-mitigated": "challenge", "content-type": "text/html"},
        data=None,
        text="challenge",
    )

    result = PMDBClient._to_delete_result(response, endpoint="/api/external/mappings/stale")

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after_seconds == 300
    assert result.error_code == "cloudflare_challenge"


def test_confirmed_challenge_mapping_create_is_retryable():
    response = APIResponse(
        status=403,
        headers={"cf-mitigated": "challenge", "content-type": "text/html"},
        data=None,
        text="challenge",
    )

    result = PMDBClient._to_submit_result(response, endpoint="/api/external/mappings")

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after_seconds == 300
    assert result.error_code == "cloudflare_challenge"


def test_401_gate_pause_behavior_is_unchanged():
    response = APIResponse(
        status=401,
        headers={"cf-ray": "abc123-SJC"},
        data={"error": "unauthorized"},
        text='{"error":"unauthorized"}',
    )
    api_gate, contribution_gate = _observe(response)
    assert api_gate.paused == [(300, "PMDB unauthorized")]
    assert contribution_gate.paused == [(300, "PMDB unauthorized")]


class _FakeEpisodeBatchDB:
    def __init__(self):
        self.failed: list[tuple] = []
        self.retried: list[tuple] = []
        self.submitted: list[tuple] = []

    def mark_episode_rating_failed(self, tmdb_id, media_type, season, episode, label, error_text):
        self.failed.append((tmdb_id, media_type, season, episode, label, error_text))

    def mark_episode_rating_retry(
        self, tmdb_id, media_type, season, episode, label, retry_at, error_text
    ):
        self.retried.append((tmdb_id, media_type, season, episode, label, retry_at, error_text))

    def mark_episode_rating_submitted(
        self, tmdb_id, media_type, season, episode, label, submitted_at, pmdb_item_id=None
    ):
        self.submitted.append(
            (tmdb_id, media_type, season, episode, label, submitted_at, pmdb_item_id)
        )


class _FakeEpisodeBatchClient:
    def __init__(self, batch_response: APIResponse):
        self._batch_response = batch_response

    async def submit_episode_ratings_batch(self, **kwargs):
        return self._batch_response

    async def confirm_episode_rating_exists(self, **kwargs):
        return False, None

    async def delete_episode_rating_by_id(self, rating_id):  # pragma: no cover - unused here
        raise AssertionError("no cached pmdb_item_id in this scenario")


def _format_manual_error(*, endpoint, status, code, retryable, message):
    return f"{status} {code} retryable={retryable} {message}"


def test_episode_batch_json_403_with_cf_ray_is_not_treated_as_cloudflare_challenge():
    batch_response = APIResponse(
        status=403,
        headers={"content-type": "application/json", "cf-ray": "abc123-SJC"},
        data={"error": "forbidden"},
        text='{"error":"forbidden"}',
    )
    row = {
        "tmdb_id": 10,
        "media_type": "tv",
        "season": 1,
        "label": "IM",
        "episode": 3,
        "pmdb_item_id": None,
        "score": 80.0,
        "pmdb_attempts": 0,
    }
    db = _FakeEpisodeBatchDB()

    asyncio.run(
        submit_episode_ratings_batch(
            rows=[row],
            pmdb_client=_FakeEpisodeBatchClient(batch_response),
            db=db,
            max_retry_attempts=5,
            format_manual_error_fn=_format_manual_error,
            retry_delay_seconds_fn=lambda result, attempts: 30,
            now_epoch_fn=lambda: 1_000,
            first_non_empty_fn=first_non_empty,
            parse_int_fn=parse_int,
            parse_retry_after_fn=parse_retry_after,
            pmdb_submit_result_cls=PMDBSubmitResult,
            extract_error_code_fn=PMDBClient._extract_error_code,
            is_cloudflare_challenge_fn=PMDBClient._is_cloudflare_challenge,
            logger=_NullLogger(),
        )
    )

    # A plain JSON 403 is non-retryable (consistent with how 403s are
    # handled everywhere else in the PMDB provider), so the row is marked
    # permanently failed -- it must NOT be scheduled for the 300s
    # Cloudflare-challenge retry that the cf-ray false positive used to
    # trigger.
    assert db.retried == []
    assert len(db.failed) == 1
    assert "403" in db.failed[0][-1]


def test_episode_batch_genuine_cloudflare_challenge_still_gets_300s_retry():
    batch_response = APIResponse(
        status=403,
        headers={"content-type": "text/html"},
        data=None,
        text="Just a moment...",
    )
    row = {
        "tmdb_id": 10,
        "media_type": "tv",
        "season": 1,
        "label": "IM",
        "episode": 3,
        "pmdb_item_id": None,
        "score": 80.0,
        "pmdb_attempts": 0,
    }
    db = _FakeEpisodeBatchDB()
    seen_retry_after: list[int] = []

    asyncio.run(
        submit_episode_ratings_batch(
            rows=[row],
            pmdb_client=_FakeEpisodeBatchClient(batch_response),
            db=db,
            max_retry_attempts=5,
            format_manual_error_fn=_format_manual_error,
            retry_delay_seconds_fn=lambda result, attempts: seen_retry_after.append(
                result.retry_after_seconds
            )
            or result.retry_after_seconds,
            now_epoch_fn=lambda: 1_000,
            first_non_empty_fn=first_non_empty,
            parse_int_fn=parse_int,
            parse_retry_after_fn=parse_retry_after,
            pmdb_submit_result_cls=PMDBSubmitResult,
            extract_error_code_fn=PMDBClient._extract_error_code,
            is_cloudflare_challenge_fn=PMDBClient._is_cloudflare_challenge,
            logger=_NullLogger(),
        )
    )

    assert db.failed == []
    assert len(db.retried) == 1
    assert seen_retry_after == [300]
