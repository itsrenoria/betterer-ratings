"""Regression coverage for the 409 duplicate_unresolved mapping bug.

The title-scoped GET /api/external/mappings collection is not an
authoritative view of PMDB's create-uniqueness check: it can omit or
misreport entries that PMDB itself considers a duplicate. Confirming a
create-time duplicate/conflict must instead use the reverse ownership
lookup, GET /api/external/mappings/lookup?id_type=&id_value=&media_type=.
"""

from __future__ import annotations

import asyncio

from betterer_ratings.domain.models import APIResponse
from betterer_ratings.providers.pmdb_client import PMDBClient
from betterer_ratings.providers.pmdb_submission_mapping import (
    resolve_mapping_duplicate_or_conflict,
)


class _FakeClient:
    def __init__(self, response: APIResponse):
        self._response = response
        self.calls: list[tuple] = []

    async def _fetch_mapping_owners(self, *, id_type, id_value, media_type):
        self.calls.append((id_type, id_value, media_type))
        return self._response

    _mapping_lookup_owned_by = staticmethod(PMDBClient._mapping_lookup_owned_by)
    _extract_error_code = staticmethod(PMDBClient._extract_error_code)


def _resolve(response: APIResponse, **kwargs):
    client = _FakeClient(response)
    result = asyncio.run(resolve_mapping_duplicate_or_conflict(client, **kwargs))
    return client, result


def _response(*, status=200, headers=None, data=None, text=""):
    return APIResponse(status=status, headers=headers or {}, data=data, text=text)


def test_resolves_real_case_tvdb_owned_by_multiple_titles_including_us():
    # tmdb 46195 / tv / tvdb / 102261 -- captured payload showed two owners,
    # one of them our own tmdb_id, while the title-scoped GET had no tvdb
    # entry for 46195 at all.
    response = _response(
        data={
            "results": [
                {"tmdb_id": 46195, "media_type": "tv"},
                {"tmdb_id": 290689, "media_type": "tv"},
            ],
            "total": 2,
        }
    )
    client, result = _resolve(
        response,
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="102261",
    )
    assert client.calls == [("tvdb", "102261", "tv")]
    assert result.success is True
    assert result.duplicate_or_exists is True
    assert result.error_code == "exists"
    assert result.status_code == 200


def test_resolves_real_case_trakt_single_owner_matching_us():
    # tmdb 46195 / tv / trakt / 45938 -- title-scoped GET had no trakt entry
    # at all for 46195, but the reverse lookup confirmed sole ownership.
    response = _response(data={"results": [{"tmdb_id": 46195, "media_type": "tv"}], "total": 1})
    client, result = _resolve(
        response,
        tmdb_id=46195,
        media_type="tv",
        id_type="trakt",
        id_value="45938",
    )
    assert client.calls == [("trakt", "45938", "tv")]
    assert result.success is True
    assert result.duplicate_or_exists is True
    assert result.error_code == "exists"


def test_resolves_real_case_tvdb_owned_by_us_despite_mismatched_title_scoped_value():
    # tmdb 12609 / tv / tvdb / 76666 -- the title-scoped GET returned a
    # different tvdb value (295068) for the same title, but the reverse
    # lookup confirmed 12609 owns tvdb=76666.
    response = _response(data={"results": [{"tmdb_id": 12609, "media_type": "tv"}], "total": 1})
    client, result = _resolve(
        response,
        tmdb_id=12609,
        media_type="tv",
        id_type="tvdb",
        id_value="76666",
    )
    assert result.success is True
    assert result.duplicate_or_exists is True
    assert result.error_code == "exists"


def test_owned_by_other_title_is_non_retryable_and_distinctly_coded():
    response = _response(data={"results": [{"tmdb_id": 290689, "media_type": "tv"}], "total": 1})
    _client, result = _resolve(
        response,
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="102261",
    )
    assert result.success is False
    assert result.retryable is False
    assert result.duplicate_or_exists is False
    assert result.error_code == "mapping_owned_by_other"
    assert result.status_code == 409


def test_explicit_other_owner_does_not_depend_on_total_metadata():
    for payload in (
        {"results": [{"tmdb_id": 290689, "media_type": "tv"}]},
        {"results": [{"tmdb_id": 290689, "media_type": "tv"}], "total": "1"},
        {"results": [{"tmdb_id": 290689, "media_type": "tv"}], "total": 0},
    ):
        _client, result = _resolve(
            _response(data=payload),
            tmdb_id=46195,
            media_type="tv",
            id_type="tvdb",
            id_value="102261",
        )

        assert result.success is False
        assert result.retryable is False
        assert result.error_code == "mapping_owned_by_other"


def test_malformed_owner_entries_remain_duplicate_unresolved():
    response = _response(
        data={
            "results": [
                {"tmdb_id": True, "media_type": "tv"},
                {"tmdb_id": 290689.5, "media_type": "tv"},
                {"tmdb_id": 290689},
                "not-an-owner",
            ],
            "total": 4,
        }
    )
    _client, result = _resolve(
        response,
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="102261",
    )

    assert result.success is False
    assert result.error_code == "duplicate_unresolved"


def test_zero_owners_falls_back_to_duplicate_unresolved():
    response = _response(data={"results": [], "total": 0})
    _client, result = _resolve(
        response,
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="999999",
    )
    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "duplicate_unresolved"
    assert result.status_code == 409


def test_transient_lookup_statuses_stay_retryable():
    for status in (429, 500, 502, 503, 504, 0):
        response = _response(status=status, text="boom")
        _client, result = _resolve(
            response,
            tmdb_id=46195,
            media_type="tv",
            id_type="tvdb",
            id_value="102261",
        )
        assert result.success is False
        assert result.retryable is True
        assert result.status_code == status
        assert result.endpoint == "/api/external/mappings/lookup"


def test_unauthorized_lookup_is_retryable_forbidden_is_not():
    _client, unauthorized = _resolve(
        _response(status=401, text="nope"),
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="102261",
    )
    assert unauthorized.retryable is True

    _client, forbidden = _resolve(
        _response(status=403, text="nope"),
        tmdb_id=46195,
        media_type="tv",
        id_type="tvdb",
        id_value="102261",
    )
    assert forbidden.retryable is False
    assert forbidden.success is False


def test_lookup_400_404_422_are_not_converted_into_duplicate_unresolved():
    # A lookup-level error is not evidence about ownership either way --
    # it must surface as its own failure, distinct from the "lookup
    # succeeded but found no owners" duplicate_unresolved fallback.
    for status in (400, 404, 422):
        response = _response(
            status=status,
            data={"error": f"lookup failed with {status}"},
            text=f'{{"error":"lookup failed with {status}"}}',
        )
        _client, result = _resolve(
            response,
            tmdb_id=46195,
            media_type="tv",
            id_type="tvdb",
            id_value="102261",
        )
        assert result.success is False
        assert result.retryable is False
        assert result.status_code == status
        assert result.error_code != "duplicate_unresolved"
        assert result.error_code == f"lookup failed with {status}"
        assert result.endpoint == "/api/external/mappings/lookup"
