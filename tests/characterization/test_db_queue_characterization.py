from tests import support as m


def _save_enriched(
    db,
    *,
    tmdb_id,
    media_type,
    now_ts,
    ratings=None,
    mappings=None,
    enrichment_error=None,
):
    return db.save_enriched_item(
        tmdb_id=tmdb_id,
        media_type=media_type,
        title=f"Title {tmdb_id}",
        imdb_id="tt1234567",
        popularity=1.0,
        tmdb_vote_average=70.0,
        enrichment_error=enrichment_error,
        ratings=ratings or {},
        mappings=mappings or {},
        now_ts=now_ts,
    )


def _episode_entry(parent_imdb, episode_imdb, season, episode, score=80.0, votes=100):
    return m.IMDbEpisodeArchiveCandidate(
        parent_imdb_id=parent_imdb,
        episode_imdb_id=episode_imdb,
        season=season,
        episode=episode,
        score=score,
        votes=votes,
    )


def test_claim_next_pending_rating_marks_in_flight_and_uses_fetched_order(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=1, media_type="movie", now_ts=100, ratings={"IM": 70.0})
    _save_enriched(db, tmdb_id=2, media_type="movie", now_ts=200, ratings={"IM": 71.0})

    first = db.claim_next_pending_rating(now_ts=500)
    assert first is not None
    assert int(first["tmdb_id"]) == 1

    first_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_claimed_at FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (1, "movie", "IM"),
    ).fetchone()
    assert dict(first_row) == {"pmdb_status": "in_flight", "pmdb_claimed_at": 500}

    second = db.claim_next_pending_rating(now_ts=500)
    assert second is not None
    assert int(second["tmdb_id"]) == 2
    assert db.claim_next_pending_rating(now_ts=500) is None


def test_claim_next_pending_rating_keeps_fetched_order_across_pending_and_retry(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=70, media_type="movie", now_ts=100, ratings={"IM": 70.0})
    _save_enriched(db, tmdb_id=71, media_type="movie", now_ts=200, ratings={"IM": 71.0})

    first = db.claim_next_pending_rating(now_ts=500)
    assert first is not None
    assert int(first["tmdb_id"]) == 70

    db.mark_rating_retry(
        tmdb_id=70,
        media_type="movie",
        label="IM",
        retry_after=0,
        error_text="retry",
    )

    second = db.claim_next_pending_rating(now_ts=500)
    assert second is not None
    assert int(second["tmdb_id"]) == 70

    third = db.claim_next_pending_rating(now_ts=500)
    assert third is not None
    assert int(third["tmdb_id"]) == 71


def test_claim_next_pending_mapping_marks_in_flight_and_uses_fetched_order(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=10, media_type="movie", now_ts=100, mappings={"tvdb": "100"})
    _save_enriched(db, tmdb_id=11, media_type="movie", now_ts=200, mappings={"tvdb": "200"})

    first = db.claim_next_pending_mapping(now_ts=500)
    assert first is not None
    assert int(first["tmdb_id"]) == 10

    first_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_claimed_at FROM mappings WHERE tmdb_id = ? AND media_type = ? AND id_type = ?",
        (10, "movie", "tvdb"),
    ).fetchone()
    assert dict(first_row) == {"pmdb_status": "in_flight", "pmdb_claimed_at": 500}

    second = db.claim_next_pending_mapping(now_ts=500)
    assert second is not None
    assert int(second["tmdb_id"]) == 11
    assert db.claim_next_pending_mapping(now_ts=500) is None


def test_claim_next_pending_mapping_keeps_fetched_order_across_pending_and_retry(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=80, media_type="movie", now_ts=100, mappings={"tvdb": "80"})
    _save_enriched(db, tmdb_id=81, media_type="movie", now_ts=200, mappings={"tvdb": "81"})

    first = db.claim_next_pending_mapping(now_ts=500)
    assert first is not None
    assert int(first["tmdb_id"]) == 80

    db.mark_mapping_retry(
        tmdb_id=80,
        media_type="movie",
        id_type="tvdb",
        retry_after=0,
        error_text="retry",
    )

    second = db.claim_next_pending_mapping(now_ts=500)
    assert second is not None
    assert int(second["tmdb_id"]) == 80

    third = db.claim_next_pending_mapping(now_ts=500)
    assert third is not None
    assert int(third["tmdb_id"]) == 81


def test_rating_retry_then_failed_transitions_increment_attempts(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=3, media_type="movie", now_ts=100, ratings={"IM": 72.0})
    assert db.claim_next_pending_rating(now_ts=500) is not None

    db.mark_rating_retry(
        tmdb_id=3,
        media_type="movie",
        label="IM",
        retry_after=600,
        error_text="temporary",
    )

    retry_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_claimed_at, pmdb_retry_after, pmdb_last_error "
        "FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (3, "movie", "IM"),
    ).fetchone()
    assert dict(retry_row) == {
        "pmdb_status": "retry",
        "pmdb_attempts": 1,
        "pmdb_claimed_at": None,
        "pmdb_retry_after": 600,
        "pmdb_last_error": "temporary",
    }

    assert db.claim_next_pending_rating(now_ts=599) is None
    assert db.claim_next_pending_rating(now_ts=600) is not None

    db.mark_rating_failed(tmdb_id=3, media_type="movie", label="IM", error_text="fatal")
    failed_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_claimed_at, pmdb_last_error "
        "FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (3, "movie", "IM"),
    ).fetchone()
    assert dict(failed_row) == {
        "pmdb_status": "failed",
        "pmdb_attempts": 2,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "fatal",
    }


def test_mark_rating_submitted_updates_metrics_and_submitted_titles(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=4, media_type="movie", now_ts=100, ratings={"IM": 73.0})
    assert db.claim_next_pending_rating(now_ts=500) is not None

    submitted_at = 1700000000
    db.mark_rating_submitted(
        tmdb_id=4,
        media_type="movie",
        label="IM",
        submitted_at=submitted_at,
        pmdb_item_id="pmdb-rating-4",
    )

    row = db.conn.execute(
        "SELECT pmdb_status, pmdb_item_id, pmdb_submitted_at, pmdb_retry_after, pmdb_last_error "
        "FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (4, "movie", "IM"),
    ).fetchone()
    assert dict(row) == {
        "pmdb_status": "submitted",
        "pmdb_item_id": "pmdb-rating-4",
        "pmdb_submitted_at": submitted_at,
        "pmdb_retry_after": 0,
        "pmdb_last_error": None,
    }

    day_key = m.local_day_key(submitted_at)
    assert db.get_state_int("metrics:pmdb_submitted:ratings:total", 0) == 1
    assert db.get_state_int(f"metrics:pmdb_submitted:ratings:day:{day_key}", 0) == 1

    submitted_title = db.conn.execute(
        "SELECT tmdb_id, media_type FROM submitted_titles WHERE tmdb_id = ? AND media_type = ?",
        (4, "movie"),
    ).fetchone()
    assert dict(submitted_title) == {"tmdb_id": 4, "media_type": "movie"}


def test_clear_rating_pmdb_item_id_nulls_out_cached_id(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=5, media_type="movie", now_ts=100, ratings={"IM": 73.0})
    db.mark_rating_submitted(
        tmdb_id=5,
        media_type="movie",
        label="IM",
        submitted_at=1700000000,
        pmdb_item_id="foreign-rating-id",
    )

    db.clear_rating_pmdb_item_id(tmdb_id=5, media_type="movie", label="IM")

    row = db.conn.execute(
        "SELECT pmdb_item_id, pmdb_status FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (5, "movie", "IM"),
    ).fetchone()
    assert dict(row) == {"pmdb_item_id": None, "pmdb_status": "submitted"}


def test_mapping_retry_submitted_and_failed_transitions(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=20, media_type="movie", now_ts=100, mappings={"tvdb": "20"})
    assert db.claim_next_pending_mapping(now_ts=500) is not None

    db.mark_mapping_retry(
        tmdb_id=20,
        media_type="movie",
        id_type="tvdb",
        retry_after=700,
        error_text="pause",
    )
    retry_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_retry_after, pmdb_claimed_at, pmdb_last_error "
        "FROM mappings WHERE tmdb_id = ? AND media_type = ? AND id_type = ?",
        (20, "movie", "tvdb"),
    ).fetchone()
    assert dict(retry_row) == {
        "pmdb_status": "retry",
        "pmdb_attempts": 1,
        "pmdb_retry_after": 700,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "pause",
    }

    assert db.claim_next_pending_mapping(now_ts=699) is None
    assert db.claim_next_pending_mapping(now_ts=700) is not None

    submitted_at = 1700000100
    db.mark_mapping_submitted(
        tmdb_id=20,
        media_type="movie",
        id_type="tvdb",
        submitted_at=submitted_at,
        pmdb_item_id="pmdb-mapping-20",
    )
    submitted_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_item_id, pmdb_submitted_at, pmdb_attempts "
        "FROM mappings WHERE tmdb_id = ? AND media_type = ? AND id_type = ?",
        (20, "movie", "tvdb"),
    ).fetchone()
    assert dict(submitted_row) == {
        "pmdb_status": "submitted",
        "pmdb_item_id": "pmdb-mapping-20",
        "pmdb_submitted_at": submitted_at,
        "pmdb_attempts": 1,
    }

    day_key = m.local_day_key(submitted_at)
    assert db.get_state_int("metrics:pmdb_submitted:mappings:total", 0) == 1
    assert db.get_state_int(f"metrics:pmdb_submitted:mappings:day:{day_key}", 0) == 1

    db.mark_mapping_failed(tmdb_id=20, media_type="movie", id_type="tvdb", error_text="final")
    failed_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_last_error FROM mappings "
        "WHERE tmdb_id = ? AND media_type = ? AND id_type = ?",
        (20, "movie", "tvdb"),
    ).fetchone()
    assert dict(failed_row) == {
        "pmdb_status": "failed",
        "pmdb_attempts": 2,
        "pmdb_last_error": "final",
    }


def test_episode_claim_batch_groups_by_first_pending_season_and_orders_episodes(local_db):
    db = local_db
    queued = db.save_imdb_episode_ratings(
        tmdb_id=30,
        media_type="tv",
        imdb_parent_id="tt1111111",
        entries=[
            _episode_entry("tt1111111", "tt2000002", season=1, episode=2),
            _episode_entry("tt1111111", "tt2000001", season=1, episode=1),
        ],
        now_ts=100,
    )
    assert queued == 2
    queued += db.save_imdb_episode_ratings(
        tmdb_id=30,
        media_type="tv",
        imdb_parent_id="tt1111111",
        entries=[_episode_entry("tt1111111", "tt3000001", season=2, episode=1)],
        now_ts=200,
    )
    assert queued == 3

    rows = db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=100)
    assert len(rows) == 2
    assert [int(row["episode"]) for row in rows] == [1, 2]
    assert {int(row["season"]) for row in rows} == {1}

    season_1_status = db.conn.execute(
        "SELECT DISTINCT pmdb_status, pmdb_claimed_at FROM episode_ratings "
        "WHERE tmdb_id = ? AND media_type = ? AND season = ?",
        (30, "tv", 1),
    ).fetchall()
    assert [dict(row) for row in season_1_status] == [
        {"pmdb_status": "in_flight", "pmdb_claimed_at": 500}
    ]

    season_2_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_claimed_at FROM episode_ratings "
        "WHERE tmdb_id = ? AND media_type = ? AND season = ? AND episode = ?",
        (30, "tv", 2, 1),
    ).fetchone()
    assert dict(season_2_row) == {"pmdb_status": "pending", "pmdb_claimed_at": None}


def test_imdb_episode_ratings_default_to_imdb_label(local_db):
    db = local_db

    assert (
        db.save_imdb_episode_ratings(
            tmdb_id=33,
            media_type="tv",
            imdb_parent_id="tt3333333",
            entries=[_episode_entry("tt3333333", "tt5000001", season=1, episode=1)],
            now_ts=100,
        )
        == 1
    )

    labels = db.conn.execute(
        "SELECT label FROM episode_ratings WHERE tmdb_id = ? AND media_type = ?",
        (33, "tv"),
    ).fetchall()

    assert [str(row["label"]) for row in labels] == ["IM"]


def test_episode_claim_batch_size_is_clamped_to_fifty(local_db):
    db = local_db
    entries = [
        _episode_entry("tt2222222", f"tt4{episode:06d}", season=1, episode=episode)
        for episode in range(1, 56)
    ]
    assert (
        db.save_imdb_episode_ratings(
            tmdb_id=31,
            media_type="tv",
            imdb_parent_id="tt2222222",
            entries=entries,
            now_ts=100,
        )
        == 55
    )

    rows = db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=500)
    assert len(rows) == 50
    assert int(rows[0]["episode"]) == 1
    assert int(rows[-1]["episode"]) == 50


def test_episode_retry_submitted_and_failed_transitions(local_db):
    db = local_db
    assert (
        db.save_imdb_episode_ratings(
            tmdb_id=32,
            media_type="tv",
            imdb_parent_id="tt3333333",
            entries=[_episode_entry("tt3333333", "tt5000001", season=1, episode=1)],
            now_ts=100,
        )
        == 1
    )
    rows = db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=10)
    assert len(rows) == 1

    db.mark_episode_rating_retry(
        tmdb_id=32,
        media_type="tv",
        season=1,
        episode=1,
        label="IM",
        retry_after=800,
        error_text="wait",
    )
    retry_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_retry_after, pmdb_claimed_at, pmdb_last_error "
        "FROM episode_ratings WHERE tmdb_id = ? AND media_type = ? AND season = ? AND episode = ? AND label = ?",
        (32, "tv", 1, 1, "IM"),
    ).fetchone()
    assert dict(retry_row) == {
        "pmdb_status": "retry",
        "pmdb_attempts": 1,
        "pmdb_retry_after": 800,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "wait",
    }

    assert db.claim_next_pending_episode_ratings_batch(now_ts=799, batch_size=10) == []
    assert len(db.claim_next_pending_episode_ratings_batch(now_ts=800, batch_size=10)) == 1

    submitted_at = 1700000200
    db.mark_episode_rating_submitted(
        tmdb_id=32,
        media_type="tv",
        season=1,
        episode=1,
        label="IM",
        submitted_at=submitted_at,
        pmdb_item_id="pmdb-episode-1",
    )
    submitted_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_item_id, pmdb_submitted_at, pmdb_attempts FROM episode_ratings "
        "WHERE tmdb_id = ? AND media_type = ? AND season = ? AND episode = ? AND label = ?",
        (32, "tv", 1, 1, "IM"),
    ).fetchone()
    assert dict(submitted_row) == {
        "pmdb_status": "submitted",
        "pmdb_item_id": "pmdb-episode-1",
        "pmdb_submitted_at": submitted_at,
        "pmdb_attempts": 1,
    }

    day_key = m.local_day_key(submitted_at)
    assert db.get_state_int("metrics:pmdb_submitted:episode_ratings:total", 0) == 1
    assert db.get_state_int(f"metrics:pmdb_submitted:episode_ratings:day:{day_key}", 0) == 1

    db.mark_episode_rating_failed(
        tmdb_id=32,
        media_type="tv",
        season=1,
        episode=1,
        label="IM",
        error_text="failed",
    )
    failed_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_attempts, pmdb_last_error FROM episode_ratings "
        "WHERE tmdb_id = ? AND media_type = ? AND season = ? AND episode = ? AND label = ?",
        (32, "tv", 1, 1, "IM"),
    ).fetchone()
    assert dict(failed_row) == {
        "pmdb_status": "failed",
        "pmdb_attempts": 2,
        "pmdb_last_error": "failed",
    }


def test_recover_in_flight_rows_moves_all_queues_to_retry(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=40, media_type="movie", now_ts=100, ratings={"IM": 70.0})
    _save_enriched(db, tmdb_id=41, media_type="movie", now_ts=100, mappings={"tvdb": "41"})
    db.save_imdb_episode_ratings(
        tmdb_id=42,
        media_type="tv",
        imdb_parent_id="tt4444444",
        entries=[_episode_entry("tt4444444", "tt6000001", season=1, episode=1)],
        now_ts=100,
    )

    assert db.claim_next_pending_rating(now_ts=500) is not None
    assert db.claim_next_pending_mapping(now_ts=500) is not None
    assert len(db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=10)) == 1

    db.recover_in_flight_rows()

    rating_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_retry_after, pmdb_claimed_at, pmdb_last_error "
        "FROM ratings WHERE tmdb_id = ? AND media_type = ? AND label = ?",
        (40, "movie", "IM"),
    ).fetchone()
    assert dict(rating_row) == {
        "pmdb_status": "retry",
        "pmdb_retry_after": 0,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "Recovered from in_flight",
    }

    mapping_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_retry_after, pmdb_claimed_at, pmdb_last_error "
        "FROM mappings WHERE tmdb_id = ? AND media_type = ? AND id_type = ?",
        (41, "movie", "tvdb"),
    ).fetchone()
    assert dict(mapping_row) == {
        "pmdb_status": "retry",
        "pmdb_retry_after": 0,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "Recovered from in_flight",
    }

    episode_row = db.conn.execute(
        "SELECT pmdb_status, pmdb_retry_after, pmdb_claimed_at, pmdb_last_error FROM episode_ratings "
        "WHERE tmdb_id = ? AND media_type = ? AND season = ? AND episode = ? AND label = ?",
        (42, "tv", 1, 1, "IM"),
    ).fetchone()
    assert dict(episode_row) == {
        "pmdb_status": "retry",
        "pmdb_retry_after": 0,
        "pmdb_claimed_at": None,
        "pmdb_last_error": "Recovered from in_flight",
    }


def test_queue_counts_and_count_due_queue_characterization(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=50, media_type="movie", now_ts=100, ratings={"IM": 75.0})
    _save_enriched(db, tmdb_id=51, media_type="movie", now_ts=100, mappings={"tvdb": "51"})
    _save_enriched(db, tmdb_id=52, media_type="movie", now_ts=100, mappings={"tvdb": "52"})
    db.save_imdb_episode_ratings(
        tmdb_id=53,
        media_type="tv",
        imdb_parent_id="tt5555555",
        entries=[_episode_entry("tt5555555", "tt7000001", season=1, episode=1)],
        now_ts=100,
    )

    assert db.claim_next_pending_rating(now_ts=500) is not None
    db.mark_rating_retry(
        tmdb_id=50,
        media_type="movie",
        label="IM",
        retry_after=900,
        error_text="later",
    )

    assert db.claim_next_pending_mapping(now_ts=500) is not None

    episode_rows = db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=10)
    assert len(episode_rows) == 1
    db.mark_episode_rating_failed(
        tmdb_id=53,
        media_type="tv",
        season=1,
        episode=1,
        label="IM",
        error_text="bad",
    )

    counts = db.queue_counts()
    assert counts["ratings_pending"] == 1
    assert counts["mappings_pending"] == 1
    assert counts["mappings_in_flight"] == 1
    assert counts["episode_ratings_failed"] == 1

    assert db.count_due_queue(kind="ratings", now_ts=899) == 0
    assert db.count_due_queue(kind="rating", now_ts=900) == 1
    assert db.count_due_queue(kind="episodes", now_ts=1000) == 0
    assert db.count_due_queue(kind="anything-else", now_ts=1000) == 1


def test_next_due_queue_kind_selects_oldest_due_work_across_queues(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=50, media_type="movie", now_ts=300, ratings={"IM": 75.0})
    _save_enriched(db, tmdb_id=51, media_type="movie", now_ts=200, mappings={"tvdb": "51"})
    db.save_imdb_episode_ratings(
        tmdb_id=52,
        media_type="tv",
        imdb_parent_id="tt1000001",
        entries=[_episode_entry("tt1000001", "tt1000002", season=1, episode=1)],
        now_ts=100,
    )

    assert db.next_due_queue_kind(now_ts=500) == "episode_ratings"

    db.claim_next_pending_episode_ratings_batch(now_ts=500, batch_size=50)
    assert db.next_due_queue_kind(now_ts=500) == "mapping"

    db.claim_next_pending_mapping(now_ts=500)
    assert db.next_due_queue_kind(now_ts=500) == "rating"


def test_local_due_titles_are_prioritized_failed_then_stale_then_new(local_db):
    db = local_db
    _save_enriched(db, tmdb_id=60, media_type="movie", now_ts=950)
    _save_enriched(db, tmdb_id=61, media_type="movie", now_ts=700)
    _save_enriched(db, tmdb_id=62, media_type="movie", now_ts=980, enrichment_error="transient")
    _save_enriched(db, tmdb_id=63, media_type="movie", now_ts=900, enrichment_error="transient")
    db.conn.execute(
        """
        INSERT INTO titles(tmdb_id, media_type, title, imdb_id, popularity, tmdb_vote_average, last_seen_at, last_harvested_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (64, "movie", "New", None, 0.0, None, 1000),
    )

    rows = db.select_local_due_titles(
        now_ts=1000,
        ratings_ttl_seconds=100,
        failed_retry_seconds=50,
        limit=10,
    )

    assert [(row["tmdb_id"], row["harvest_reason"]) for row in rows] == [
        (63, "failed"),
        (61, "ttl"),
        (64, "new"),
    ]
