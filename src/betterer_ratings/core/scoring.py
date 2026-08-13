from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Tuple


def clamp_0_100(value: float) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    if value <= 0:
        return None
    bounded = min(100.0, max(0.0, value))
    rounded = Decimal(str(bounded)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(rounded)


def score_to_tenths(value: Any) -> Optional[int]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    normalized = clamp_0_100(numeric)
    if normalized is None:
        return None
    return int(round(normalized * 10.0))


def parse_value_and_scale(value: Any) -> Tuple[Optional[float], Optional[float]]:
    if value is None:
        return None, None

    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric, None
        return None, None

    text = str(value).strip()
    if not text:
        return None, None

    lowered = text.lower()
    if lowered in {"n/a", "none", "null", "nan"}:
        return None, None

    text = text.replace("%", "").strip()

    if "/" in text:
        left, right = text.split("/", 1)
        try:
            num = float(left.strip())
            den = float(right.strip())
            if not (math.isfinite(num) and math.isfinite(den)):
                return None, None
            return num, den
        except ValueError:
            return None, None

    try:
        num = float(text)
        if not math.isfinite(num):
            return None, None
        return num, None
    except ValueError:
        return None, None


def scale_to_100(
    numeric: Optional[float],
    denominator: Optional[float] = None,
    default_scale_hint: Optional[int] = None,
) -> Optional[float]:
    if numeric is None:
        return None

    if denominator is not None and denominator > 0:
        return clamp_0_100((numeric / denominator) * 100.0)

    if default_scale_hint == 4:
        if numeric <= 4:
            return clamp_0_100(numeric * 25.0)
        return clamp_0_100(numeric)
    if default_scale_hint == 5:
        if numeric <= 5:
            return clamp_0_100(numeric * 20.0)
        if numeric <= 10:
            return clamp_0_100(numeric * 10.0)
        return clamp_0_100(numeric)
    if default_scale_hint == 10:
        if numeric <= 10:
            return clamp_0_100(numeric * 10.0)
        return clamp_0_100(numeric)

    if numeric <= 5:
        return clamp_0_100(numeric * 20.0)
    if numeric <= 10:
        return clamp_0_100(numeric * 10.0)

    return clamp_0_100(numeric)


def normalized_mdblist_source_score(entry: Dict[str, Any]) -> Optional[float]:
    """MDBList rating entries expose score as already normalized to 0-100."""

    score_num, score_den = parse_value_and_scale(entry.get("score"))
    if score_num is None:
        return None
    if score_den is not None:
        return scale_to_100(score_num, score_den)
    return clamp_0_100(score_num)


def percent_value_to_100(
    numeric: Optional[float],
    denominator: Optional[float] = None,
) -> Optional[float]:
    if numeric is None:
        return None
    if denominator is not None:
        return scale_to_100(numeric, denominator)
    return clamp_0_100(numeric)


def parse_mdblist_ratings(mdblist_data: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Parse ratings into PMDB labels on a 0-100 scale."""

    if not mdblist_data:
        return {}

    ratings: Dict[str, float] = {}

    metascore_raw = mdblist_data.get("Metascore")
    if metascore_raw is None:
        metascore_raw = mdblist_data.get("metascore")

    metascore_num, metascore_den = parse_value_and_scale(metascore_raw)
    if metascore_num is not None:
        if metascore_den is None:
            mc_score = clamp_0_100(metascore_num)
        else:
            mc_score = scale_to_100(metascore_num, metascore_den)
        if mc_score is not None:
            ratings["MC"] = mc_score

    imdb_top_raw = mdblist_data.get("imdbRating")
    if imdb_top_raw is None:
        imdb_top_raw = mdblist_data.get("imdbrating")

    imdb_top_num, imdb_top_den = parse_value_and_scale(imdb_top_raw)
    imdb_top_score = scale_to_100(imdb_top_num, imdb_top_den, default_scale_hint=10)
    if imdb_top_score is not None:
        ratings["IM"] = imdb_top_score

    for key in ("tmdbRating", "tmdb_rating", "tmdbVoteAverage", "tmdb_vote_average"):
        tmdb_raw = mdblist_data.get(key)
        tmdb_num, tmdb_den = parse_value_and_scale(tmdb_raw)
        tmdb_score = scale_to_100(tmdb_num, tmdb_den, default_scale_hint=10)
        if tmdb_score is not None:
            ratings["TM"] = tmdb_score
            break

    raw_ratings = mdblist_data.get("ratings")
    if not isinstance(raw_ratings, list):
        raw_ratings = []

    for entry in raw_ratings:
        if not isinstance(entry, dict):
            continue

        source = str(entry.get("source", "")).strip().lower()
        if not source:
            continue

        source_score = normalized_mdblist_source_score(entry)
        value_num, value_den = parse_value_and_scale(entry.get("value"))

        if source in {"imdb", "internet movie database"}:
            if "IM" not in ratings:
                im_score = source_score
                if im_score is None:
                    im_score = scale_to_100(value_num, value_den, default_scale_hint=10)
                if im_score is not None:
                    ratings["IM"] = im_score
            continue

        if source in {"rotten tomatoes", "tomatoes", "tomatometer"}:
            if "audience" not in source:
                rt_score = source_score
                if rt_score is None:
                    rt_score = percent_value_to_100(value_num, value_den)
                if rt_score is not None:
                    ratings["RT"] = rt_score
            continue

        if "audience" in source or "popcorn" in source:
            pc_score = source_score
            if pc_score is None:
                pc_score = percent_value_to_100(value_num, value_den)
            if pc_score is not None:
                ratings["PC"] = pc_score
            continue

        if source.startswith("metacritic"):
            if "user" in source:
                continue
            if source_score is not None:
                ratings["MC"] = source_score
                continue
            if value_num is None:
                continue
            if value_den == 10 or (value_den is None and value_num <= 10):
                continue
            if "MC" not in ratings:
                mc_score = percent_value_to_100(value_num, value_den)
                if mc_score is not None:
                    ratings["MC"] = mc_score
            continue

        if "letterboxd" in source:
            lb_score = source_score
            if lb_score is None:
                lb_score = scale_to_100(value_num, value_den, default_scale_hint=10)
            if lb_score is not None:
                ratings["LB"] = lb_score
            continue

        if "trakt" in source:
            tr_score = source_score
            if tr_score is None:
                tr_score = percent_value_to_100(value_num, value_den)
            if tr_score is not None:
                ratings["TR"] = tr_score
            continue

        if source in {"tmdb", "the movie database"} or "themoviedb" in source:
            if "TM" not in ratings:
                tm_score = source_score
                if tm_score is None:
                    tm_score = percent_value_to_100(value_num, value_den)
                if tm_score is not None:
                    ratings["TM"] = tm_score
            continue

        if source in {"mal", "myanimelist", "my anime list"}:
            ml_score = source_score
            if ml_score is None:
                ml_score = scale_to_100(value_num, value_den, default_scale_hint=10)
            if ml_score is not None:
                ratings["ML"] = ml_score
            continue

        if source in {"roger ebert", "rogerebert", "roger-ebert"}:
            re_score = source_score
            if re_score is None:
                re_score = scale_to_100(value_num, value_den, default_scale_hint=4)
            if re_score is not None:
                ratings["RE"] = re_score
            continue

    if "TR" not in ratings:
        trakt_fallback = mdblist_data.get("score")
        tr_num, tr_den = parse_value_and_scale(trakt_fallback)
        if tr_num is None:
            tr_score = None
        elif tr_den is not None:
            tr_score = scale_to_100(tr_num, tr_den)
        else:
            tr_score = clamp_0_100(tr_num)
        if tr_score is not None:
            ratings["TR"] = tr_score

    return ratings


def parse_tmdb_vote_average(tmdb_details: Optional[Dict[str, Any]]) -> Optional[float]:
    if not tmdb_details:
        return None
    vote = tmdb_details.get("vote_average")
    if vote is None:
        return None
    try:
        return clamp_0_100(float(vote) * 10.0)
    except (TypeError, ValueError):
        return None
