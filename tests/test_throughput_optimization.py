import asyncio

from betterer_ratings.domain.models import APIResponse
from betterer_ratings.services.submit.handler_mapping import submit_mapping_group
from betterer_ratings.services.submit.worker import queue_order_for_worker
from tests.characterization.test_db_queue_characterization import _save_enriched


def test_claim_mapping_group_claims_all_due_mappings_for_oldest_title(local_db):
    _save_enriched(
        local_db,
        tmdb_id=10,
        media_type="movie",
        now_ts=100,
        mappings={"imdb": "tt10", "tvdb": "tv10", "trakt": "tr10"},
    )
    _save_enriched(
        local_db,
        tmdb_id=11,
        media_type="movie",
        now_ts=200,
        mappings={"imdb": "tt11"},
    )

    rows = local_db.claim_next_pending_mapping_group(now_ts=500)

    assert len(rows) == 3
    assert {str(row["id_type"]) for row in rows} == {"imdb", "tvdb", "trakt"}
    assert {int(row["tmdb_id"]) for row in rows} == {10}
    statuses = local_db.conn.execute(
        "SELECT DISTINCT pmdb_status FROM mappings WHERE tmdb_id = 10"
    ).fetchall()
    assert [str(row["pmdb_status"]) for row in statuses] == ["in_flight"]
    remaining = local_db.conn.execute(
        "SELECT pmdb_status FROM mappings WHERE tmdb_id = 11"
    ).fetchone()
    assert str(remaining["pmdb_status"]) == "pending"


def test_sixteen_workers_reserve_capacity_for_mappings_ratings_and_episodes():
    preferred = [queue_order_for_worker(worker_id, 16)[0] for worker_id in range(1, 17)]

    assert preferred.count("mapping") == 8
    assert preferred.count("rating") == 6
    assert preferred.count("episode_ratings") == 2


def test_mapping_group_preflight_resolves_existing_and_posts_only_missing():
    class FakePMDB:
        async def _fetch_existing_mappings(self, tmdb_id, media_type):
            assert (tmdb_id, media_type) == (10, "movie")
            return APIResponse(
                status=200,
                headers={},
                data={
                    "mappings": {
                        "imdb": [{"id": "remote-imdb", "value": "tt10"}],
                        "tvdb": [{"id": "remote-tvdb", "value": "tv10"}],
                    }
                },
                text="",
            )

        @staticmethod
        def _extract_mappings_for_type(payload, id_type):
            return payload.get("mappings", {}).get(id_type, [])

        @staticmethod
        def _mapping_entry_matches_value(entry, id_value):
            return entry.get("value") == id_value

        @staticmethod
        def _extract_entry_id(entry):
            return entry.get("id")

    class FakeDB:
        def __init__(self):
            self.submitted = []

        def mark_mapping_submitted(
            self, tmdb_id, media_type, id_type, submitted_at, pmdb_item_id=None
        ):
            self.submitted.append((tmdb_id, media_type, id_type, submitted_at, pmdb_item_id))

    posted = []

    async def post_missing(*, row):
        posted.append(str(row["id_type"]))

    db = FakeDB()
    asyncio.run(
        submit_mapping_group(
            rows=[
                {"tmdb_id": 10, "media_type": "movie", "id_type": "imdb", "id_value": "tt10"},
                {"tmdb_id": 10, "media_type": "movie", "id_type": "tvdb", "id_value": "tv10"},
                {"tmdb_id": 10, "media_type": "movie", "id_type": "trakt", "id_value": "tr10"},
            ],
            pmdb_client=FakePMDB(),
            db=db,
            submit_mapping_fn=post_missing,
            now_epoch_fn=lambda: 500,
            logger=type("Logger", (), {"info": lambda *args, **kwargs: None})(),
        )
    )

    assert {item[2] for item in db.submitted} == {"imdb", "tvdb"}
    assert {item[4] for item in db.submitted} == {None}
    assert posted == ["trakt"]
