from __future__ import annotations

import json
from typing import Any


def format_pmdb_error(
    *,
    endpoint_hint: str,
    result: Any,
) -> str:
    payload = {
        "service": "pmdb",
        "endpoint": getattr(result, "endpoint", "") or endpoint_hint,
        "status": int(getattr(result, "status_code", 0) or 0),
        "code": (getattr(result, "error_code", "") or "unknown")[:80],
        "retryable": bool(getattr(result, "retryable", False)),
        "message": " ".join(str(getattr(result, "error_text", "") or "").split())[:320],
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def format_manual_error(
    *,
    endpoint: str,
    status: int,
    code: str,
    retryable: bool,
    message: str,
) -> str:
    payload = {
        "service": "pmdb",
        "endpoint": endpoint,
        "status": int(status),
        "code": str(code)[:80],
        "retryable": bool(retryable),
        "message": " ".join(str(message).split())[:320],
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def retry_delay_seconds(*, retry_after_seconds: int, current_attempts: int) -> int:
    attempt_index = max(1, int(current_attempts) + 1)
    base = max(5, int(retry_after_seconds or 30))
    multiplier = min(2 ** max(0, attempt_index - 1), 64)
    delay = max(base, min(base * multiplier, 6 * 3600))
    return max(5, int(delay))
