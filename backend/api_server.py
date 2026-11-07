"""REST API, the server itself: /api/health, metrics (Prometheus), server_status. A Flask
blueprint (#306); contracts/api.md.
"""

import hmac
import logging
import sqlite3
import time

from flask import Blueprint, Response, abort, jsonify, request

import state
from config import (
    HEALTH_MAX_MESSAGE_AGE, METRICS_TOKEN,
)
from store import (
    db,
)
import metrics
import server_checks
from api_common import require_auth

log = logging.getLogger(__name__)
bp = Blueprint("server", __name__)


@bp.route("/api/health")
def health():
    """For an external uptime monitor (#77): no auth and no sensor data.

    503 when MQTT is disconnected, no live message came for HEALTH_MAX_MESSAGE_AGE
    (counted from startup until the first one), or the DB can't be read.
    """
    now = time.time()
    last_age = int(now - state.LAST_MESSAGE_AT) if state.LAST_MESSAGE_AT else None
    try:
        with db() as conn:
            conn.execute("SELECT 1 FROM system_state LIMIT 1").fetchall()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    mqtt_ok = state.mqttc is not None and state.mqttc.is_connected()
    fresh = now - (state.LAST_MESSAGE_AT or state.STARTED_AT) < HEALTH_MAX_MESSAGE_AGE
    ok = mqtt_ok and fresh and db_ok
    body = {"ok": ok, "mqtt_connected": mqtt_ok, "last_message_age_s": last_age, "db_ok": db_ok}
    return jsonify(body), 200 if ok else 503


@bp.route("/api/metrics")
def metrics_api():
    """Prometheus metrics: node health, the newest sensor values, the backend's state.
    A bearer token of its own (.env METRICS_TOKEN), not the app's login, so the scraper
    holds nothing that opens the rest of the API. No token configured: 404."""
    if not METRICS_TOKEN:
        abort(404)
    given = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not hmac.compare_digest(given.encode(), METRICS_TOKEN.encode()):
        return Response("Authentication required", 401, {"WWW-Authenticate": "Bearer"})
    return Response(metrics.render(), mimetype="text/plain", content_type="text/plain; version=0.0.4; charset=utf-8")


@bp.route("/api/server_status")
@require_auth
def server_status():
    """The server watchdog's view for the app's Server card (#260); behind auth, unlike
    /api/health: disk space and host names are nobody else's business."""
    return jsonify(server_checks.status())
