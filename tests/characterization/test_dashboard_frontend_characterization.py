from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from betterer_ratings import api_server


def _dashboard_html() -> str:
    return (api_server.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


def test_dashboard_serves_only_meridian_frontend() -> None:
    app = api_server.create_app(db=object())
    paths = {
        route.resource.canonical
        for route in app.router.routes()
        if getattr(route.resource, "canonical", None)
    }

    assert "/" in paths
    assert "/v2" not in paths
    assert "/v2.html" not in paths
    assert "/v3" not in paths
    assert "/v3.html" not in paths
    assert (api_server.FRONTEND_DIR / "index.html").exists()
    assert not (api_server.FRONTEND_DIR / "v2.html").exists()
    assert not (api_server.FRONTEND_DIR / "v3.html").exists()

    assert any(
        route.method == "GET" and getattr(route.resource, "canonical", None) == "/api/logs"
        for route in app.router.routes()
    )


def test_meridian_header_and_tables_match_single_dashboard_copy() -> None:
    html = _dashboard_html()

    assert "<title>betterer-ratings</title>" in html
    assert "<span>betterer-ratings</span>" in html
    assert 'id="activity-matrix"' in html
    assert "MATRIX_LAYOUT" in html
    assert "renderActivityMatrix()" in html
    assert "s.service || s.name" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "PMDB<small> Meridian</small>" not in html
    assert "MERIDIAN" not in html
    assert "v2" not in html
    assert 'href="/">V1' not in html
    assert 'href="/v3"' not in html
    assert "<th class=\"r\">Avg</th>" not in html
    assert "sparkSVG(" not in html
    assert "enr</span>" not in html


def test_meridian_relative_time_normalizes_epoch_seconds() -> None:
    html = _dashboard_html()

    assert "function epochMillis(ts)" in html
    assert "numeric < 1000000000000 ? numeric * 1000 : numeric" in html
    assert "new Date(epochMillis(ts))" in html


def test_meridian_trend_chart_is_interactive_stacked_columns() -> None:
    html = _dashboard_html()

    assert "id=\"trend-tooltip\"" in html
    assert "function showTrendTooltip" in html
    assert "trend-segment" in html
    assert "trend-hit" in html
    assert "trend-legend" in html
    assert "pointermove" in html
    assert "function stackArea" not in html
    assert "<polyline" not in html
    assert 'preserveAspectRatio="xMidYMid meet"' in html
    assert '<g transform="translate(${PAD_L},${H - 16})">' not in html


def test_activity_uses_recent_titles_as_the_page_scroll() -> None:
    html = _dashboard_html()

    assert "activity-grid" in html
    assert "recent-table compact" in html
    assert "error-chip" in html
    assert "Daily Submitted Titles" not in html
    assert "daily-titles-chart" not in html
    assert "renderDailyTitles" not in html
    assert "table-scroll" not in html


def test_overview_cards_use_quiet_metric_hierarchy() -> None:
    html = _dashboard_html()

    assert "cls:'stat-titles'" in html
    assert "cls:'stat-pending'" in html
    assert "stat-accent" in html
    assert "stat-card.pending" not in html
    assert "stat-card.failed" not in html
    assert ".stat-card .stat-value{font-family:var(--font-mono);font-size:1.42rem" in html
    assert ".stat-card .stat-total{font-family:var(--font-mono);font-size:.72rem" in html


def test_logs_tab_provides_level_filter_and_client_side_clear() -> None:
    html = _dashboard_html()

    assert 'data-tab="logs"' in html
    assert 'id="tab-logs"' in html
    assert 'id="log-level-filter"' in html
    assert 'id="log-clear-btn"' in html
    assert 'id="log-list"' in html
    assert "function renderLogs()" in html
    assert "/api/logs" in html
    assert "logsClearedAtEpoch = logsNewestVisibleTimestamp" in html

    clear_handler_start = html.index("log-clear-btn')?.addEventListener")
    clear_handler_end = html.index("});", clear_handler_start)
    clear_handler_body = html[clear_handler_start:clear_handler_end]
    assert "loadAll()" not in clear_handler_body
    assert "fetchJSON" not in clear_handler_body
    assert "Date.now()" not in clear_handler_body


def test_services_api_hides_expired_pause_reason() -> None:
    class FakeDB:
        def get_service_state(self, service: str) -> dict[str, object] | None:
            if service == "tmdb":
                return {
                    "service": "tmdb",
                    "paused_until": 1,
                    "pause_reason": "Network unavailable",
                    "rate_limit": None,
                    "rate_remaining": None,
                    "rate_reset": None,
                    "last_status": 200,
                    "updated_at": 1,
                }
            if service == "mdblist":
                return {
                    "service": "mdblist",
                    "paused_until": 9999999999,
                    "pause_reason": "Daily limit reached",
                    "rate_limit": 1000,
                    "rate_remaining": 0,
                    "rate_reset": 9999999999,
                    "last_status": 429,
                    "updated_at": 1,
                }
            return None

    response = asyncio.run(api_server.handle_services(SimpleNamespace(app={"db": FakeDB()})))
    payload = json.loads(response.text)
    services = {svc["service"]: svc for svc in payload["services"]}

    assert services["tmdb"]["is_paused"] is False
    assert services["tmdb"]["pause_remaining_seconds"] == 0
    assert services["tmdb"]["pause_reason"] is None
    assert services["mdblist"]["is_paused"] is True
    assert services["mdblist"]["pause_reason"] == "Daily limit reached"
