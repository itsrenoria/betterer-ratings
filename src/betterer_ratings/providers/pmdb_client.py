from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Tuple

from betterer_ratings.config.schema import PMDBConfig
from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult, PMDBSubmitResult
from betterer_ratings.infra.http.client import HTTPClient
from betterer_ratings.providers import pmdb_helpers as provider_pmdb_helpers
from betterer_ratings.providers import pmdb_lookup as provider_pmdb_lookup
from betterer_ratings.providers import pmdb_transport as provider_pmdb_transport
from betterer_ratings.providers.pmdb_submission_episode import (
    delete_episode_rating_by_id,
    submit_episode_ratings_batch,
)
from betterer_ratings.providers.pmdb_submission_mapping import (
    delete_mapping_by_id,
    resolve_mapping_duplicate_or_conflict,
    submit_mapping,
)
from betterer_ratings.providers.pmdb_submission_rating import (
    delete_rating_by_id,
    replace_rating_after_duplicate,
    submit_rating,
)


class PMDBClient:
    _USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        api_key: str,
        config: PMDBConfig,
        api_gate: Any,
        rating_gate: Any,
        mapping_gate: Any,
        logger: Optional[logging.Logger] = None,
    ):
        self.api_key = api_key
        self.base_url = config.base_url.rstrip("/")
        self.http = HTTPClient(
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        self.api_gate = api_gate
        self.rating_gate = rating_gate
        self.mapping_gate = mapping_gate
        self._logger = logger or logging.getLogger("betterer-ratings")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._USER_AGENT,
        }

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self._USER_AGENT,
        }

    async def _post_with_gates(
        self,
        *,
        url: str,
        payload: Dict[str, Any],
        contribution_gate: Any,
    ) -> APIResponse:
        return await provider_pmdb_transport.post_with_gates(
            self,
            url=url,
            payload=payload,
            contribution_gate=contribution_gate,
        )

    async def _delete_with_gates(
        self,
        *,
        url: str,
        contribution_gate: Any,
    ) -> APIResponse:
        return await provider_pmdb_transport.delete_with_gates(
            self,
            url=url,
            contribution_gate=contribution_gate,
        )

    def _observe_submission_response(
        self,
        response: APIResponse,
        contribution_gate: Any,
        *,
        method: str,
        endpoint: str,
    ) -> None:
        provider_pmdb_transport.observe_submission_response(
            self,
            response,
            contribution_gate,
            method=method,
            endpoint=endpoint,
        )

    @staticmethod
    def _extract_item_id(payload: Any) -> Optional[str]:
        return provider_pmdb_helpers.extract_item_id(payload)

    @staticmethod
    def _extract_error_code(payload: Any, text: str) -> str:
        return provider_pmdb_helpers.extract_error_code(payload, text)

    @staticmethod
    def _is_cloudflare_challenge(response: APIResponse) -> bool:
        return provider_pmdb_helpers.is_cloudflare_challenge(response)

    @staticmethod
    def _is_create_failed_rating(result: PMDBSubmitResult) -> bool:
        return provider_pmdb_helpers.is_create_failed_rating(result)

    @staticmethod
    def _is_create_failed_mapping(result: PMDBSubmitResult) -> bool:
        return provider_pmdb_helpers.is_create_failed_mapping(result)

    @staticmethod
    def _is_duplicate_or_exists_result(result: PMDBSubmitResult) -> bool:
        return provider_pmdb_helpers.is_duplicate_or_exists_result(result)

    @staticmethod
    def _extract_entry_id(entry: Dict[str, Any]) -> Optional[str]:
        return provider_pmdb_helpers.extract_entry_id(entry)

    @staticmethod
    def _to_submit_result(response: APIResponse, endpoint: str = "") -> PMDBSubmitResult:
        return provider_pmdb_helpers.to_submit_result(response, endpoint=endpoint)

    @staticmethod
    def _to_delete_result(response: APIResponse, endpoint: str = "") -> PMDBDeleteResult:
        return provider_pmdb_helpers.to_delete_result(response, endpoint=endpoint)

    async def _fetch_existing_ratings(self, tmdb_id: int, media_type: str) -> APIResponse:
        return await provider_pmdb_lookup.fetch_existing_ratings(self, tmdb_id, media_type)

    async def _fetch_existing_episode_ratings(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        season: int,
        episode: int,
        label: str,
    ) -> APIResponse:
        return await provider_pmdb_lookup.fetch_existing_episode_ratings(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=label,
        )

    async def _fetch_existing_mappings(self, tmdb_id: int, media_type: str) -> APIResponse:
        return await provider_pmdb_lookup.fetch_existing_mappings(self, tmdb_id, media_type)

    async def _fetch_mapping_owners(
        self,
        *,
        id_type: str,
        id_value: str,
        media_type: str,
    ) -> APIResponse:
        return await provider_pmdb_lookup.fetch_mapping_owners(
            self,
            id_type=id_type,
            id_value=id_value,
            media_type=media_type,
        )

    @staticmethod
    def _extract_ratings_for_label(payload: Any, label: str) -> list[Dict[str, Any]]:
        return provider_pmdb_helpers.extract_ratings_for_label(payload, label)

    @staticmethod
    def _extract_mappings_for_type(payload: Any, id_type: str) -> list[Dict[str, Any]]:
        return provider_pmdb_helpers.extract_mappings_for_type(payload, id_type)

    @staticmethod
    def _rating_entry_matches_score(entry: Dict[str, Any], score: float) -> bool:
        return provider_pmdb_helpers.rating_entry_matches_score(entry, score)

    @staticmethod
    def _mapping_entry_matches_value(entry: Dict[str, Any], id_value: str) -> bool:
        return provider_pmdb_helpers.mapping_entry_matches_value(entry, id_value)

    @staticmethod
    def _mapping_lookup_owned_by(payload: Any, tmdb_id: int, media_type: str) -> bool:
        return provider_pmdb_helpers.mapping_lookup_owned_by(payload, tmdb_id, media_type)

    async def confirm_rating_exists(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        label: str,
        score: float,
    ) -> Tuple[bool, Optional[str]]:
        return await provider_pmdb_lookup.confirm_rating_exists(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            score=score,
        )

    async def confirm_episode_rating_exists(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        season: int,
        episode: int,
        label: str,
        score: float,
    ) -> Tuple[bool, Optional[str]]:
        return await provider_pmdb_lookup.confirm_episode_rating_exists(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            label=label,
            score=score,
        )

    async def confirm_mapping_exists(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        id_value: str,
    ) -> Tuple[bool, Optional[str]]:
        return await provider_pmdb_lookup.confirm_mapping_exists(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            id_value=id_value,
        )

    async def _delete_rating_by_id(self, rating_id: str) -> PMDBDeleteResult:
        return await delete_rating_by_id(self, rating_id)

    async def _delete_episode_rating_by_id(self, rating_id: str) -> PMDBDeleteResult:
        return await delete_episode_rating_by_id(self, rating_id)

    async def _delete_mapping_by_id(self, mapping_id: str) -> PMDBDeleteResult:
        return await delete_mapping_by_id(self, mapping_id)

    async def _replace_rating_after_duplicate(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        label: str,
        score: float,
        known_item_id: Optional[str],
    ) -> PMDBSubmitResult:
        return await replace_rating_after_duplicate(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            score=score,
            known_item_id=known_item_id,
        )

    async def _resolve_mapping_duplicate_or_conflict(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        id_value: str,
    ) -> PMDBSubmitResult:
        return await resolve_mapping_duplicate_or_conflict(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            id_value=id_value,
        )

    async def submit_rating(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        label: str,
        score: float,
        existing_pmdb_item_id: Optional[str] = None,
    ) -> PMDBSubmitResult:
        return await submit_rating(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            label=label,
            score=score,
            existing_pmdb_item_id=existing_pmdb_item_id,
        )

    async def submit_mapping(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        id_type: str,
        id_value: str,
    ) -> PMDBSubmitResult:
        return await submit_mapping(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            id_type=id_type,
            id_value=id_value,
        )

    async def submit_episode_ratings_batch(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        season: int,
        label: str,
        ratings: Sequence[Dict[str, Any]],
    ) -> APIResponse:
        return await submit_episode_ratings_batch(
            self,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=season,
            label=label,
            ratings=ratings,
        )

    async def delete_episode_rating_by_id(self, rating_id: str) -> PMDBDeleteResult:
        return await self._delete_episode_rating_by_id(rating_id)

    async def aclose(self) -> None:
        await self.http.aclose()
