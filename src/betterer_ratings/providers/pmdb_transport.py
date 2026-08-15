from __future__ import annotations

from typing import Any, Dict, Optional, cast

from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.core.urls import sanitize_url_for_logs
from betterer_ratings.domain.models import APIResponse


def _paused_response(gate: Any, text: str) -> APIResponse:
    pause_remaining_fn = getattr(gate, "pause_remaining", None)
    remaining = (
        int(max(1, pause_remaining_fn())) if callable(pause_remaining_fn) else 1
    )
    return APIResponse(
        status=429,
        headers={"retry-after": str(remaining)},
        data={"error": text, "retry_after": remaining},
        text=text,
    )


async def post_with_gates(
    client: Any,
    *,
    url: str,
    payload: Dict[str, Any],
    contribution_gate: Any,
) -> APIResponse:
    # Respect both global API rate and contribution/action-specific rate.
    acquired_global = await client.api_gate.acquire(
        max_pause_wait_seconds=client.max_pause_block_seconds
    )
    if not acquired_global:
        return _paused_response(client.api_gate, "global gate paused")

    acquired_action = await contribution_gate.acquire(
        max_pause_wait_seconds=client.max_pause_block_seconds
    )
    if not acquired_action:
        return _paused_response(contribution_gate, "action gate paused")

    response = await client.http.request_json(
        method="POST",
        url=url,
        headers=client._headers(),
        json_body=payload,
        gate=None,
        max_pause_wait_seconds=client.max_pause_block_seconds,
    )

    observe_submission_response(
        client,
        response,
        contribution_gate,
        method="POST",
        endpoint=url,
    )

    return cast(APIResponse, response)


async def delete_with_gates(
    client: Any,
    *,
    url: str,
    contribution_gate: Any,
    payload: Optional[Dict[str, Any]] = None,
) -> APIResponse:
    acquired_global = await client.api_gate.acquire(
        max_pause_wait_seconds=client.max_pause_block_seconds
    )
    if not acquired_global:
        return _paused_response(client.api_gate, "global gate paused")

    acquired_action = await contribution_gate.acquire(
        max_pause_wait_seconds=client.max_pause_block_seconds
    )
    if not acquired_action:
        return _paused_response(contribution_gate, "action gate paused")

    response = await client.http.request_json(
        method="DELETE",
        url=url,
        headers=client._auth_headers(),
        json_body=payload,
        gate=None,
        max_pause_wait_seconds=client.max_pause_block_seconds,
    )
    observe_submission_response(
        client,
        response,
        contribution_gate,
        method="DELETE",
        endpoint=url,
    )
    return cast(APIResponse, response)


def observe_submission_response(
    client: Any,
    response: APIResponse,
    contribution_gate: Any,
    *,
    method: str,
    endpoint: str,
) -> None:
    # Mirror returned status into service-state rows for visibility.
    client.api_gate.observe_headers(response.headers, response.status)
    contribution_gate.observe_headers(response.headers, response.status)
    safe_endpoint = sanitize_url_for_logs(endpoint)
    error_code = client._extract_error_code(response.data, response.text or "")
    error_suffix = f" code={error_code}" if error_code else ""

    is_cloudflare_challenge = client._is_cloudflare_challenge(response)

    if is_cloudflare_challenge:
        retry_after = 300
        client.api_gate.pause_for(retry_after, "Cloudflare challenge")
        contribution_gate.pause_for(retry_after, "Cloudflare challenge")
        client._logger.warning(
            "[PMDB] Cloudflare challenge on %s %s. Pausing PMDB gates for %ss.",
            method,
            safe_endpoint,
            retry_after,
            extra={
                "event": "pmdb.challenge_detected",
                "method": method,
                "endpoint": safe_endpoint,
                "retry_after_seconds": retry_after,
            },
        )
    elif response.status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"), 5)
        client.api_gate.pause_for(retry_after, "PMDB 429")
        contribution_gate.pause_for(retry_after, "PMDB contribution 429")

    elif response.status == 401:
        client.api_gate.pause_for(300, "PMDB unauthorized")
        contribution_gate.pause_for(300, "PMDB unauthorized")
        client._logger.error(
            "[PMDB] 401 Unauthorized on %s %s%s. Pausing PMDB gates for 300s.",
            method,
            safe_endpoint,
            error_suffix,
        )
    elif response.status == 403:
        client._logger.warning(
            "[PMDB] 403 Forbidden on %s %s%s. Treating as non-retryable item-level conflict (no global pause).",
            method,
            safe_endpoint,
            error_suffix,
        )
