from tests import support as m


def _response(*, status, headers=None, data=None, text=""):
    return m.APIResponse(
        status=status,
        headers=headers or {},
        data=data,
        text=text,
    )


def test_extract_item_id_prefers_nested_item_id_then_top_level_id():
    assert m.PMDBClient._extract_item_id({"item": {"id": "item-1"}, "id": "top-1"}) == "item-1"
    assert m.PMDBClient._extract_item_id({"id": "top-2"}) == "top-2"
    assert m.PMDBClient._extract_item_id({"item": {"id": "  "}, "id": ""}) is None


def test_extract_error_code_prefers_payload_fields_then_text_fallbacks():
    payload = {"error_code": "payload_code", "code": "ignored_code"}
    assert m.PMDBClient._extract_error_code(payload, "duplicate") == "payload_code"

    assert m.PMDBClient._extract_error_code({}, "Already Exists") == "exists"
    assert m.PMDBClient._extract_error_code({}, "Duplicate item") == "duplicate"
    assert m.PMDBClient._extract_error_code({}, "rate limit reached") == "rate_limited"


def test_to_submit_result_success_contains_item_id_and_endpoint():
    result = m.PMDBClient._to_submit_result(
        _response(status=201, data={"item": {"id": "pmdb-1"}}),
        endpoint="/api/external/ratings",
    )

    assert result.success is True
    assert result.retryable is False
    assert result.retry_after_seconds == 0
    assert result.duplicate_or_exists is False
    assert result.error_text == ""
    assert result.item_id == "pmdb-1"
    assert result.status_code == 201
    assert result.error_code == ""
    assert result.endpoint == "/api/external/ratings"


def test_to_submit_result_retry_and_non_retry_status_buckets():
    too_many = m.PMDBClient._to_submit_result(
        _response(status=429, headers={"retry-after": "17"}, text="slow down"),
        endpoint="/api/external/ratings",
    )
    assert too_many.success is False
    assert too_many.retryable is True
    assert too_many.retry_after_seconds == 17
    assert too_many.status_code == 429

    transient = m.PMDBClient._to_submit_result(_response(status=500, text="server"))
    assert transient.retryable is True
    assert transient.retry_after_seconds == 30

    unauthorized = m.PMDBClient._to_submit_result(_response(status=401, text="auth"))
    assert unauthorized.retryable is True
    assert unauthorized.retry_after_seconds == 300

    forbidden = m.PMDBClient._to_submit_result(_response(status=403, text="forbidden"))
    assert forbidden.retryable is False
    assert forbidden.retry_after_seconds == 0

    bad_request = m.PMDBClient._to_submit_result(_response(status=400, text="bad"))
    assert bad_request.retryable is False
    assert bad_request.retry_after_seconds == 0


def test_to_delete_result_treats_ok_and_404_as_success():
    ok = m.PMDBClient._to_delete_result(_response(status=200, text="ok"))
    assert ok.success is True
    assert ok.retryable is False

    missing = m.PMDBClient._to_delete_result(_response(status=404, text="missing"))
    assert missing.success is True
    assert missing.retryable is False

    rate_limited = m.PMDBClient._to_delete_result(
        _response(status=429, headers={"retry-after": "9"}, text="slow down")
    )
    assert rate_limited.success is False
    assert rate_limited.retryable is True
    assert rate_limited.retry_after_seconds == 9


def test_extract_ratings_for_label_filters_case_insensitive_label_only():
    payload = {
        "items": [
            {"label": "IM", "score": 80},
            {"label": "im", "score": 70},
            {"label": "RT", "score": 90},
            "ignore-me",
        ]
    }

    results = m.PMDBClient._extract_ratings_for_label(payload, " im ")
    assert results == [
        {"label": "IM", "score": 80},
        {"label": "im", "score": 70},
    ]


def test_extract_mappings_for_type_supports_case_variants_and_dict_entries_only():
    payload = {
        "mappings": {
            "TVDB": [
                {"id": "1", "value": "123"},
                "ignore-me",
                {"id": "2", "value": "456"},
            ]
        }
    }

    results = m.PMDBClient._extract_mappings_for_type(payload, "tvdb")
    assert results == [
        {"id": "1", "value": "123"},
        {"id": "2", "value": "456"},
    ]


def test_rating_and_mapping_entry_matchers_use_current_normalization_rules():
    assert m.PMDBClient._rating_entry_matches_score({"score": "80.04"}, 80.0) is True
    assert m.PMDBClient._rating_entry_matches_score({"score": "80.04"}, 80.1) is False

    assert m.PMDBClient._mapping_entry_matches_value({"id_value": " TT123 "}, "tt123") is True
    assert m.PMDBClient._mapping_entry_matches_value({"value": "tt123"}, "tt999") is False


def test_mapping_lookup_owned_by_matches_tmdb_id_and_media_type_case_insensitively():
    payload = {
        "results": [
            {"tmdb_id": 46195, "media_type": "TV"},
            {"tmdb_id": 290689, "media_type": "tv"},
        ],
        "total": 2,
    }
    assert m.PMDBClient._mapping_lookup_owned_by(payload, 46195, "tv") is True
    assert m.PMDBClient._mapping_lookup_owned_by(payload, 46195, "movie") is False
    assert m.PMDBClient._mapping_lookup_owned_by(payload, 999999, "tv") is False


def test_ownership_denial_403_with_cf_ray_is_not_a_cloudflare_challenge():
    # Exact production body from the "you can only delete your own data"
    # 403s -- Cloudflare adds cf-ray to virtually every proxied response,
    # so its presence alone must not be treated as a challenge signal.
    response = _response(
        status=403,
        headers={"content-type": "application/json", "cf-ray": "8f1a2b3c4d5e6f7g-SJC"},
        data={"error": "Access denied - you can only delete your own data"},
        text='{"error":"Access denied - you can only delete your own data"}',
    )
    assert m.PMDBClient._is_cloudflare_challenge(response) is False


def test_html_403_is_a_cloudflare_challenge():
    response = _response(
        status=403,
        headers={"content-type": "text/html; charset=UTF-8"},
        text="<html><body>Attention Required!</body></html>",
    )
    assert m.PMDBClient._is_cloudflare_challenge(response) is True


def test_non_html_just_a_moment_body_is_not_a_cloudflare_challenge():
    response = _response(
        status=403,
        headers={"content-type": "application/json"},
        text="Just a moment...",
    )
    assert m.PMDBClient._is_cloudflare_challenge(response) is False


def test_json_403_with_cf_ray_and_no_challenge_markers_is_not_a_challenge():
    response = _response(
        status=403,
        headers={"content-type": "application/json", "cf-ray": "abc123-SJC"},
        data={"error": "forbidden"},
        text='{"error":"forbidden"}',
    )
    assert m.PMDBClient._is_cloudflare_challenge(response) is False


def test_non_403_status_is_never_a_cloudflare_challenge():
    response = _response(
        status=401,
        headers={"content-type": "text/html", "cf-ray": "abc123-SJC"},
        text="Just a moment...",
    )
    assert m.PMDBClient._is_cloudflare_challenge(response) is False


def test_mapping_lookup_owned_by_ignores_malformed_entries_and_payloads():
    assert m.PMDBClient._mapping_lookup_owned_by({"results": []}, 1, "movie") is False
    assert m.PMDBClient._mapping_lookup_owned_by({"total": 0}, 1, "movie") is False
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": ["not-a-dict", {"tmdb_id": "abc", "media_type": "movie"}]}, 1, "movie"
    ) is False
    assert m.PMDBClient._mapping_lookup_owned_by(None, 1, "movie") is False
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": "46195", "media_type": "tv"}]}, 46195, "tv"
    ) is True


def test_mapping_lookup_owned_by_strict_tmdb_id_coercion():
    # int entry vs int target
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": 46195, "media_type": "tv"}]}, 46195, "tv"
    ) is True
    # string entry vs int target, and the reverse framing of the same case
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": "46195", "media_type": "tv"}]}, 46195, "tv"
    ) is True
    # fractional numeric values must not match an integer tmdb_id
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": 46195.5, "media_type": "tv"}]}, 46195, "tv"
    ) is False
    # bool is a subclass of int in Python -- True/1 and False/0 must not
    # be treated as equivalent tmdb_ids
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": True, "media_type": "tv"}]}, 1, "tv"
    ) is False
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": False, "media_type": "tv"}]}, 0, "tv"
    ) is False
    # malformed/non-numeric values must not match
    assert m.PMDBClient._mapping_lookup_owned_by(
        {"results": [{"tmdb_id": "not-a-number", "media_type": "tv"}]}, 46195, "tv"
    ) is False


def test_duplicate_or_exists_detection_contract():
    by_code = m.PMDBSubmitResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        duplicate_or_exists=False,
        error_text="",
        item_id=None,
        status_code=400,
        error_code="already_exists",
        endpoint="/api/external/ratings",
    )
    assert m.PMDBClient._is_duplicate_or_exists_result(by_code) is True

    by_text = m.PMDBSubmitResult(
        success=False,
        retryable=False,
        retry_after_seconds=0,
        duplicate_or_exists=False,
        error_text="Failed: duplicate rating",
        item_id=None,
        status_code=409,
        error_code="",
        endpoint="/api/external/ratings",
    )
    assert m.PMDBClient._is_duplicate_or_exists_result(by_text) is True

    non_duplicate = m.PMDBSubmitResult(
        success=False,
        retryable=True,
        retry_after_seconds=30,
        duplicate_or_exists=False,
        error_text="temporary upstream failure",
        item_id=None,
        status_code=503,
        error_code="service_unavailable",
        endpoint="/api/external/ratings",
    )
    assert m.PMDBClient._is_duplicate_or_exists_result(non_duplicate) is False
