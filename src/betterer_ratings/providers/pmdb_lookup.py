from __future__ import annotations

from typing import Any, Optional, Tuple, cast

from betterer_ratings.domain.models import APIResponse


async def fetch_existing_ratings(
    client: Any,
    tmdb_id: int,
    media_type: str,
) -> APIResponse:
    return cast(APIResponse, await client.http.request_json(
        method="GET",
        url=f"{client.base_url}/api/external/ratings",
        headers=client._auth_headers(),
        params={
            "tmdb_id": tmdb_id,
            "media_type": media_type,
        },
        gate=client.api_gate,
    ))


async def fetch_existing_episode_ratings(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
) -> APIResponse:
    return cast(APIResponse, await client.http.request_json(
        method="GET",
        url=f"{client.base_url}/api/external/episode-ratings",
        headers=client._auth_headers(),
        params={
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "season": season,
            "episode": episode,
            "label": label,
            "perPage": 500,
        },
        gate=client.api_gate,
    ))


async def fetch_existing_mappings(
    client: Any,
    tmdb_id: int,
    media_type: str,
) -> APIResponse:
    return cast(APIResponse, await client.http.request_json(
        method="GET",
        url=f"{client.base_url}/api/external/mappings",
        headers=client._auth_headers(),
        params={
            "tmdb_id": tmdb_id,
            "media_type": media_type,
        },
        gate=client.api_gate,
    ))


async def fetch_mapping_owners(
    client: Any,
    *,
    id_type: str,
    id_value: str,
    media_type: str,
) -> APIResponse:
    return cast(APIResponse, await client.http.request_json(
        method="GET",
        url=f"{client.base_url}/api/external/mappings/lookup",
        headers=client._auth_headers(),
        params={
            "id_type": id_type,
            "id_value": id_value,
            "media_type": media_type,
        },
        gate=client.api_gate,
    ))


async def confirm_rating_exists(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    label: str,
    score: float,
) -> Tuple[bool, Optional[str]]:
    lookup = await client._fetch_existing_ratings(tmdb_id, media_type)
    if lookup.status != 200 or not isinstance(lookup.data, dict):
        return False, None
    entries = client._extract_ratings_for_label(lookup.data, label)
    for entry in entries:
        if client._rating_entry_matches_score(entry, score):
            return True, client._extract_entry_id(entry)
    return False, None


async def confirm_episode_rating_exists(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    episode: int,
    label: str,
    score: float,
) -> Tuple[bool, Optional[str]]:
    lookup = await client._fetch_existing_episode_ratings(
        tmdb_id=tmdb_id,
        media_type=media_type,
        season=season,
        episode=episode,
        label=label,
    )
    if lookup.status != 200 or not isinstance(lookup.data, dict):
        return False, None
    entries = lookup.data.get("items")
    if not isinstance(entries, list):
        return False, None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if client._rating_entry_matches_score(entry, score):
            return True, client._extract_entry_id(entry)
    return False, None


async def confirm_mapping_exists(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    id_type: str,
    id_value: str,
) -> Tuple[bool, Optional[str]]:
    lookup = await client._fetch_existing_mappings(tmdb_id, media_type)
    if lookup.status != 200 or not isinstance(lookup.data, dict):
        return False, None
    entries = client._extract_mappings_for_type(lookup.data, id_type)
    for entry in entries:
        if client._mapping_entry_matches_value(entry, id_value):
            return True, client._extract_entry_id(entry)
    return False, None
