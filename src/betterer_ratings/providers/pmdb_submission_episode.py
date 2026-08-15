from __future__ import annotations

from typing import Any, Dict, Sequence, cast

from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult


async def delete_episode_rating_by_id(client: Any, rating_id: str) -> PMDBDeleteResult:
    response = await client._delete_with_gates(
        url=f"{client.base_url}/api/external/episode-ratings/{rating_id}",
        contribution_gate=client.rating_gate,
    )
    return cast(PMDBDeleteResult, client._to_delete_result(
        response,
        endpoint=f"/api/external/episode-ratings/{rating_id}",
    ))


async def delete_episode_ratings_batch(
    client: Any,
    rating_ids: Sequence[str],
) -> APIResponse:
    return cast(
        APIResponse,
        await client._delete_with_gates(
            url=f"{client.base_url}/api/external/episode-ratings/batch",
            contribution_gate=client.rating_gate,
            payload={"ids": list(rating_ids)[:50]},
        ),
    )


async def submit_episode_ratings_batch(
    client: Any,
    *,
    tmdb_id: int,
    media_type: str,
    season: int,
    label: str,
    ratings: Sequence[Dict[str, Any]],
) -> APIResponse:
    return cast(APIResponse, await client._post_with_gates(
        url=f"{client.base_url}/api/external/episode-ratings/batch",
        payload={
            "tmdb_id": int(tmdb_id),
            "media_type": str(media_type),
            "season": int(season),
            "label": str(label),
            "ratings": list(ratings),
        },
        contribution_gate=client.rating_gate,
    ))
