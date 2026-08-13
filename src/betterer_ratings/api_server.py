"""Lightweight dashboard API server for betterer-ratings.

Runs alongside the main worker and exposes read-only JSON endpoints
from the SQLite database plus static frontend files on port 8087.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from betterer_ratings.core.clock import now_epoch
from betterer_ratings.observability import log_buffer

LOGGER = logging.getLogger("betterer-ratings.api")

DASHBOARD_PORT = 8087
FRONTEND_DIR = Path(__file__).parent / "frontend"


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _json_response(data: Any) -> web.Response:
    return web.Response(
        text=json.dumps(data, default=str),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_stats(request: web.Request) -> web.Response:
    db = request.app["db"]
    ts = now_epoch()
    queue = db.queue_counts()
    summary = db.submission_summary(ts)
    return _json_response({
        "timestamp": ts,
        "queue": queue,
        "summary": summary,
    })


async def handle_services(request: web.Request) -> web.Response:
    db = request.app["db"]
    ts = now_epoch()
    services = []
    for svc_name in ("tmdb", "mdblist", "pmdb_api", "pmdb_ratings", "pmdb_mappings"):
        row = db.get_service_state(svc_name)
        if row:
            svc = dict(row)
            paused_until = int(svc.get("paused_until") or 0)
            is_paused = paused_until > ts
            svc["is_paused"] = is_paused
            svc["pause_remaining_seconds"] = max(0, paused_until - ts)
            if not is_paused:
                svc["pause_reason"] = None
            services.append(svc)
        else:
            services.append({
                "service": svc_name,
                "paused_until": 0,
                "pause_reason": None,
                "rate_limit": None,
                "rate_remaining": None,
                "rate_reset": None,
                "last_status": None,
                "updated_at": None,
                "is_paused": False,
                "pause_remaining_seconds": 0,
            })
    return _json_response({"timestamp": ts, "services": services})


async def handle_titles_summary(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    rows = conn.execute(
        """
        SELECT
            media_type,
            COUNT(*) AS total,
            SUM(CASE WHEN last_harvested_at IS NOT NULL THEN 1 ELSE 0 END) AS enriched,
            SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS with_errors,
            SUM(CASE WHEN imdb_id IS NOT NULL THEN 1 ELSE 0 END) AS with_imdb,
            AVG(popularity) AS avg_popularity,
            AVG(tmdb_vote_average) AS avg_vote
        FROM titles
        GROUP BY media_type
        """
    ).fetchall()
    total_row = conn.execute("SELECT COUNT(*) AS c FROM titles").fetchone()
    return _json_response({
        "by_type": _rows_to_list(rows),
        "total": int(total_row["c"]) if total_row else 0,
    })


async def handle_ratings_summary(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    by_label = conn.execute(
        """
        SELECT label, COUNT(*) AS total,
            AVG(score) AS avg_score,
            SUM(CASE WHEN pmdb_status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
            SUM(CASE WHEN pmdb_status IN ('pending', 'retry') THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN pmdb_status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM ratings
        GROUP BY label
        ORDER BY total DESC
        """
    ).fetchall()
    by_status = conn.execute(
        """
        SELECT pmdb_status, COUNT(*) AS total
        FROM ratings
        GROUP BY pmdb_status
        """
    ).fetchall()
    return _json_response({
        "by_label": _rows_to_list(by_label),
        "by_status": _rows_to_list(by_status),
    })


async def handle_mappings_summary(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    by_type = conn.execute(
        """
        SELECT id_type, COUNT(*) AS total,
            SUM(CASE WHEN pmdb_status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
            SUM(CASE WHEN pmdb_status IN ('pending', 'retry') THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN pmdb_status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM mappings
        GROUP BY id_type
        ORDER BY total DESC
        """
    ).fetchall()
    by_status = conn.execute(
        """
        SELECT pmdb_status, COUNT(*) AS total
        FROM mappings
        GROUP BY pmdb_status
        """
    ).fetchall()
    return _json_response({
        "by_type": _rows_to_list(by_type),
        "by_status": _rows_to_list(by_status),
    })


async def handle_episodes_summary(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    by_status = conn.execute(
        """
        SELECT pmdb_status, COUNT(*) AS total
        FROM episode_ratings
        GROUP BY pmdb_status
        """
    ).fetchall()
    total_row = conn.execute("SELECT COUNT(*) AS c FROM episode_ratings").fetchone()
    return _json_response({
        "by_status": _rows_to_list(by_status),
        "total": int(total_row["c"]) if total_row else 0,
    })


async def handle_metrics_history(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    rows = conn.execute(
        """
        SELECT key, value FROM state
        WHERE key LIKE 'metrics:%'
        ORDER BY key
        """
    ).fetchall()

    daily: Dict[str, Dict[str, int]] = {}
    totals: Dict[str, int] = {}

    for row in rows:
        key = str(row["key"])
        val = int(row["value"]) if row["value"] else 0
        parts = key.split(":")

        if "total" in parts:
            kind = parts[2] if len(parts) > 2 else "unknown"
            totals[kind] = val
        elif "day" in parts:
            day_idx = parts.index("day")
            if day_idx + 1 < len(parts):
                day = parts[day_idx + 1]
                kind = parts[2] if len(parts) > 2 else "unknown"
                if day not in daily:
                    daily[day] = {}
                daily[day][kind] = val

    daily_sorted = [
        {"date": d, **counts}
        for d, counts in sorted(daily.items(), key=lambda x: x[0])
    ]

    return _json_response({
        "totals": totals,
        "daily": daily_sorted[-60:],
    })


async def handle_recent_titles(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    rows = conn.execute(
        """
        SELECT t.tmdb_id, t.media_type, t.title, t.imdb_id,
               t.popularity, t.tmdb_vote_average,
               t.last_harvested_at, t.last_error
        FROM titles t
        WHERE t.last_harvested_at IS NOT NULL
        ORDER BY t.last_harvested_at DESC
        LIMIT 50
        """
    ).fetchall()

    titles = []
    for row in rows:
        title_dict = dict(row)
        ratings = conn.execute(
            """
            SELECT label, score, pmdb_status
            FROM ratings
            WHERE tmdb_id = ? AND media_type = ?
            """,
            (row["tmdb_id"], row["media_type"]),
        ).fetchall()
        title_dict["ratings"] = _rows_to_list(ratings)
        titles.append(title_dict)

    return _json_response({"titles": titles})


async def handle_logs(request: web.Request) -> web.Response:
    buffer: log_buffer.LogBufferHandler = request.app["log_buffer"]
    level = request.query.get("level")
    return _json_response({"logs": buffer.snapshot(level=level)})


async def handle_submitted_titles_daily(request: web.Request) -> web.Response:
    db = request.app["db"]
    conn = db.conn
    rows = conn.execute(
        """
        SELECT day_key, COUNT(*) AS title_count
        FROM submitted_title_days
        GROUP BY day_key
        ORDER BY day_key DESC
        LIMIT 30
        """
    ).fetchall()
    return _json_response({"daily_titles": _rows_to_list(rows)})


def create_app(db: Any) -> web.Application:
    app = web.Application()
    app["db"] = db
    app["log_buffer"] = log_buffer.attach_once()

    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/services", handle_services)
    app.router.add_get("/api/titles/summary", handle_titles_summary)
    app.router.add_get("/api/ratings/summary", handle_ratings_summary)
    app.router.add_get("/api/mappings/summary", handle_mappings_summary)
    app.router.add_get("/api/episodes/summary", handle_episodes_summary)
    app.router.add_get("/api/metrics/history", handle_metrics_history)
    app.router.add_get("/api/titles/recent", handle_recent_titles)
    app.router.add_get("/api/titles/daily", handle_submitted_titles_daily)
    app.router.add_get("/api/logs", handle_logs)

    if FRONTEND_DIR.is_dir():
        app.router.add_static("/static/", FRONTEND_DIR, show_index=False)

        async def serve_dashboard(request: web.Request) -> web.StreamResponse:
            return web.FileResponse(FRONTEND_DIR / "index.html")

        app.router.add_get("/", serve_dashboard)

    return app


async def start_api_server(db: Any) -> web.AppRunner:
    app = create_app(db)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", DASHBOARD_PORT)
    await site.start()
    LOGGER.info(
        "Dashboard API server started on http://0.0.0.0:%s",
        DASHBOARD_PORT,
    )
    return runner
