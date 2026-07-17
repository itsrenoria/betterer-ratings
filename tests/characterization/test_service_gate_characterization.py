import asyncio
import logging

from betterer_ratings.infra.rate_limit.service_gate import ServiceGate


class _FakeDB:
    def __init__(self):
        self.updates = []

    def get_service_state(self, _service):
        return None

    def update_service_state(self, **kwargs):
        self.updates.append(dict(kwargs))


class _FakeLimiter:
    def __init__(self):
        self.calls = 0

    async def acquire(self):
        self.calls += 1


def _parse_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def test_observe_headers_debounces_db_writes_until_interval_elapsed():
    clock = {"now": 100}

    def now_epoch():
        return clock["now"]

    db = _FakeDB()
    limiter = _FakeLimiter()
    gate = ServiceGate(
        "tmdb",
        db,
        limiter,
        now_epoch_fn=now_epoch,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.observe_headers(
        {
            "x-ratelimit-limit": "45",
            "x-ratelimit-remaining": "44",
            "x-ratelimit-reset": "999",
        },
        200,
    )
    assert len(db.updates) == 1
    assert db.updates[-1]["rate_remaining"] == 44

    clock["now"] = 101
    gate.observe_headers(
        {
            "x-ratelimit-limit": "45",
            "x-ratelimit-remaining": "43",
            "x-ratelimit-reset": "999",
        },
        200,
    )
    assert len(db.updates) == 1

    asyncio.run(gate.acquire())
    assert limiter.calls == 1
    assert len(db.updates) == 1

    clock["now"] = 106
    asyncio.run(gate.acquire())
    assert limiter.calls == 2
    assert len(db.updates) == 2
    assert db.updates[-1]["rate_remaining"] == 43


def test_pause_for_persists_state_immediately():
    clock = {"now": 500}

    def now_epoch():
        return clock["now"]

    db = _FakeDB()
    gate = ServiceGate(
        "tmdb",
        db,
        _FakeLimiter(),
        now_epoch_fn=now_epoch,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.pause_for(120, "manual pause")
    assert len(db.updates) == 1
    assert db.updates[-1]["paused_until"] == 620
    assert db.updates[-1]["pause_reason"] == "manual pause"


def test_error_status_forces_immediate_header_state_persist():
    clock = {"now": 1000}

    def now_epoch():
        return clock["now"]

    db = _FakeDB()
    gate = ServiceGate(
        "tmdb",
        db,
        _FakeLimiter(),
        now_epoch_fn=now_epoch,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.observe_headers(
        {
            "x-ratelimit-limit": "45",
            "x-ratelimit-remaining": "42",
            "x-ratelimit-reset": "1999",
        },
        200,
    )
    assert len(db.updates) == 1

    clock["now"] = 1001
    gate.observe_headers(
        {
            "x-ratelimit-limit": "45",
            "x-ratelimit-remaining": "41",
            "x-ratelimit-reset": "1999",
        },
        503,
    )
    assert len(db.updates) == 2
    assert db.updates[-1]["last_status"] == 503
    assert db.updates[-1]["rate_remaining"] == 41


def test_daily_reserve_pauses_before_quota_hits_zero():
    db = _FakeDB()
    gate = ServiceGate(
        "mdblist",
        db,
        _FakeLimiter(),
        daily_reserve=400,
        now_epoch_fn=lambda: 1000,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    # Still above the reserve floor: no pause.
    gate.observe_headers(
        {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "401", "x-ratelimit-reset": "5000"},
        200,
    )
    assert gate.pause_remaining() == 0

    # Drops to the reserve floor: pause until the provider's reset.
    gate.observe_headers(
        {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "400", "x-ratelimit-reset": "5000"},
        200,
    )
    assert gate.paused_until == 5000
    assert "reserve floor" in gate.pause_reason.lower()


def test_daily_reserve_reuses_known_reset_when_threshold_response_omits_header():
    db = _FakeDB()
    gate = ServiceGate(
        "mdblist",
        db,
        _FakeLimiter(),
        daily_reserve=400,
        now_epoch_fn=lambda: 1000,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.observe_headers(
        {"x-ratelimit-remaining": "401", "x-ratelimit-reset": "5000"},
        200,
    )
    gate.observe_headers({"x-ratelimit-remaining": "400"}, 200)

    assert gate.rate_reset == 5000
    assert gate.paused_until == 5000


def test_daily_reserve_ignores_expired_known_reset_when_threshold_header_is_missing():
    clock = {"now": 1000}
    db = _FakeDB()
    gate = ServiceGate(
        "mdblist",
        db,
        _FakeLimiter(),
        daily_reserve=400,
        now_epoch_fn=lambda: clock["now"],
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.observe_headers(
        {"x-ratelimit-remaining": "401", "x-ratelimit-reset": "1500"},
        200,
    )
    clock["now"] = 2000
    gate.observe_headers({"x-ratelimit-remaining": "400"}, 200)

    assert gate.rate_reset == 1500
    assert gate.paused_until == 2300


def test_zero_reserve_preserves_pause_at_zero_only():
    db = _FakeDB()
    gate = ServiceGate(
        "mdblist",
        db,
        _FakeLimiter(),
        now_epoch_fn=lambda: 1000,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    gate.observe_headers(
        {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "1", "x-ratelimit-reset": "5000"},
        200,
    )
    assert gate.pause_remaining() == 0

    gate.observe_headers(
        {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "5000"},
        200,
    )
    assert gate.paused_until == 5000
    assert gate.pause_reason == "Daily Limit Reached"


def test_mdblist_observe_headers_logs_quota_state(caplog):
    db = _FakeDB()
    gate = ServiceGate(
        "mdblist",
        db,
        _FakeLimiter(),
        now_epoch_fn=lambda: 1000,
        parse_int_fn=_parse_int,
        to_iso_fn=str,
    )

    with caplog.at_level(logging.INFO, logger="betterer-ratings"):
        gate.observe_headers(
            {
                "x-ratelimit-limit": "1000",
                "x-ratelimit-remaining": "657",
                "x-ratelimit-reset": "2000",
            },
            200,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Quota updated" in message for message in messages)
    assert any("657/1000" in message for message in messages)
    quota_record = next(record for record in caplog.records if "Quota updated" in record.getMessage())
    assert "mdblist_rate_reset" not in quota_record.__dict__
    assert quota_record.mdblist_rate_reset_at == "2000"
