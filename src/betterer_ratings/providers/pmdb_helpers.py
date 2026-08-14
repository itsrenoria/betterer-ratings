from __future__ import annotations

from typing import Any, Dict, List, Optional

from betterer_ratings.core.parsing import first_non_empty, parse_int
from betterer_ratings.core.retry import parse_retry_after
from betterer_ratings.core.scoring import score_to_tenths
from betterer_ratings.domain.models import APIResponse, PMDBDeleteResult, PMDBSubmitResult


def is_cloudflare_challenge(response: APIResponse) -> bool:
    # A `cf-ray` header is present on virtually every Cloudflare-proxied
    # response -- including ordinary, legitimate JSON error responses -- so
    # it is not treated as a challenge signal here. Only markers specific to
    # an actual interstitial/challenge page are used.
    if response.status != 403:
        return False
    content_type = response.headers.get("content-type", "").lower()
    response_text = (response.text or "").lower()
    return "text/html" in content_type or "just a moment" in response_text


def extract_item_id(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        item = payload.get("item")
        if isinstance(item, dict):
            item_id = first_non_empty(item.get("id"))
            if item_id:
                return item_id
        return first_non_empty(payload.get("id"))
    return None


def extract_error_code(payload: Any, text: str) -> str:
    if isinstance(payload, dict):
        for key in ("error_code", "code", "error", "type", "status"):
            value = payload.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                return value_str[:80]
    text_norm = " ".join((text or "").split()).lower()
    if "duplicate" in text_norm:
        return "duplicate"
    if "exists" in text_norm:
        return "exists"
    if "forbidden" in text_norm:
        return "forbidden"
    if "unauthorized" in text_norm:
        return "unauthorized"
    if "rate" in text_norm and "limit" in text_norm:
        return "rate_limited"
    return ""


def is_create_failed_rating(result: PMDBSubmitResult) -> bool:
    code = str(result.error_code or "").strip().lower()
    text = str(result.error_text or "").strip().lower()
    return result.status_code == 500 and (
        "failed to create rating" in code or "failed to create rating" in text
    )


def is_create_failed_mapping(result: PMDBSubmitResult) -> bool:
    code = str(result.error_code or "").strip().lower()
    text = str(result.error_text or "").strip().lower()
    return result.status_code == 500 and (
        "failed to create id mapping" in code or "failed to create id mapping" in text
    )


def is_duplicate_or_exists_result(result: PMDBSubmitResult) -> bool:
    code = str(result.error_code or "").strip().lower()
    text = str(result.error_text or "").strip().lower()
    status = int(result.status_code or 0)
    if code in {"duplicate", "exists", "already_exists", "already-exists"}:
        return True
    if status in (400, 409, 422, 500) and (
        "duplicate" in text or "already exists" in text or "already-exists" in text
    ):
        return True
    return False


def extract_entry_id(entry: Dict[str, Any]) -> Optional[str]:
    return first_non_empty(entry.get("id"), entry.get("item_id"))


def to_submit_result(response: APIResponse, endpoint: str = "") -> PMDBSubmitResult:
    text = response.text or ""
    item_id = extract_item_id(response.data)
    error_code = extract_error_code(response.data, text)

    if response.ok:
        return PMDBSubmitResult(
            True,
            False,
            0,
            False,
            "",
            item_id,
            status_code=response.status,
            error_code="",
            endpoint=endpoint,
        )

    if response.status == 429:
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=parse_retry_after(response.headers.get("retry-after"), 10),
            duplicate_or_exists=False,
            error_text=text,
            item_id=item_id,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    if response.status in (500, 502, 503, 504, 0):
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=30,
            duplicate_or_exists=False,
            error_text=text,
            item_id=item_id,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    if response.status == 401:
        return PMDBSubmitResult(
            success=False,
            retryable=True,
            retry_after_seconds=300,
            duplicate_or_exists=False,
            error_text=text,
            item_id=item_id,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )
    if response.status == 403:
        return PMDBSubmitResult(
            success=False,
            retryable=False,
            retry_after_seconds=0,
            duplicate_or_exists=False,
            error_text=text,
            item_id=item_id,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    return PMDBSubmitResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        duplicate_or_exists=False,
        error_text=text,
        item_id=item_id,
        status_code=response.status,
        error_code=error_code,
        endpoint=endpoint,
    )


def to_delete_result(response: APIResponse, endpoint: str = "") -> PMDBDeleteResult:
    text = response.text or ""
    error_code = extract_error_code(response.data, text)
    if response.ok or response.status == 404:
        return PMDBDeleteResult(
            True,
            False,
            0,
            text,
            status_code=response.status,
            error_code="",
            endpoint=endpoint,
        )

    if response.status == 429:
        return PMDBDeleteResult(
            success=False,
            retryable=True,
            retry_after_seconds=parse_retry_after(response.headers.get("retry-after"), 10),
            error_text=text,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    if response.status in (500, 502, 503, 504, 0):
        return PMDBDeleteResult(
            success=False,
            retryable=True,
            retry_after_seconds=30,
            error_text=text,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    if response.status == 401:
        # PMDB's own `validateApiKey` answers 401 when its upstream admin
        # token is missing or stale, so a 401 here is a transient server-side
        # condition rather than a rejection of our key -- an unknown rating id
        # returns 404 and an ownership failure returns 403. Mirror
        # `to_submit_result` so the delete leg backs off instead of failing
        # the row permanently.
        return PMDBDeleteResult(
            success=False,
            retryable=True,
            retry_after_seconds=300,
            error_text=text,
            status_code=response.status,
            error_code=error_code,
            endpoint=endpoint,
        )

    return PMDBDeleteResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        error_text=text,
        status_code=response.status,
        error_code=error_code,
        endpoint=endpoint,
    )


def extract_ratings_for_label(payload: Any, label: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    wanted = label.strip().lower()
    output: List[Dict[str, Any]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        entry_label = str(entry.get("label", "")).strip().lower()
        if entry_label == wanted:
            output.append(entry)
    return output


def extract_mappings_for_type(payload: Any, id_type: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        return []

    entries: Optional[Any] = None
    lookup_keys = (id_type, id_type.lower(), id_type.upper())
    for key in lookup_keys:
        if key in mappings:
            entries = mappings[key]
            break

    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def rating_entry_matches_score(entry: Dict[str, Any], score: float) -> bool:
    existing_tenths = score_to_tenths(entry.get("score"))
    target_tenths = score_to_tenths(score)
    if existing_tenths is None or target_tenths is None:
        return False
    return existing_tenths == target_tenths


def mapping_lookup_owned_by(payload: Any, tmdb_id: int, media_type: str) -> bool:
    if not isinstance(payload, dict):
        return False
    results = payload.get("results")
    if not isinstance(results, list):
        return False

    wanted_media_type = media_type.strip().lower()
    for entry in results:
        if not isinstance(entry, dict):
            continue
        # parse_int rejects bools (str(True) == "True", not "1") and
        # fractional values (str(46195.5) can't be parsed by int()), so
        # neither can falsely coerce into matching an integer tmdb_id.
        entry_tmdb_id = parse_int(entry.get("tmdb_id"))
        if entry_tmdb_id is None:
            continue
        entry_media_type = str(entry.get("media_type", "")).strip().lower()
        if entry_tmdb_id == tmdb_id and entry_media_type == wanted_media_type:
            return True
    return False


def mapping_entry_matches_value(entry: Dict[str, Any], id_value: str) -> bool:
    existing_value = first_non_empty(
        entry.get("value"),
        entry.get("id_value"),
    )
    if not existing_value:
        return False
    return existing_value.strip().lower() == str(id_value).strip().lower()
