"""REST API, notifications: /api/notification_settings, notification_history, occupancy_arm,
occupancy_status. A Flask blueprint (#306); contracts/api.md.
"""

import logging
import re

from flask import Blueprint, jsonify, request

from config import (
    HHMM, NOTIFY_KINDS,
)
from store import (
    db, get_state, log_event, set_state,
)
from alerts import notification_settings
from air import window_open_on_leave
from api_common import iso, json_body, require_auth

log = logging.getLogger(__name__)
bp = Blueprint("notify", __name__)


@bp.route("/api/occupancy_arm", methods=["POST"])
@require_auth
def occupancy_arm():
    data = json_body()
    if not isinstance(data.get("armed"), bool):
        return jsonify({"error": '"armed" (true/false) required'}), 400
    # Who changed it (#238): "manual" (the switch, the default) or "location" (the phone's geofence)
    source = data.get("source", "manual")
    if source not in ("manual", "location"):
        return jsonify({"error": '"source" must be "manual" or "location"'}), 400
    armed = "1" if data["armed"] else "0"
    set_state("occupancy_armed", armed)
    log_event("system", "OCCUPANCY_ARMED", f"{armed} ({source})")
    if armed == "1" and source == "location":   # the phone just left home (#245)
        try:
            window_open_on_leave()
        except Exception as e:
            log.warning("window check on leaving failed: %s", e)
    return jsonify({"armed": armed == "1"})


@bp.route("/api/occupancy_status")
@require_auth
def occupancy_status():
    return jsonify({"armed": get_state("occupancy_armed") == "1"})


@bp.route("/api/notification_settings", methods=["GET", "POST"])
@require_auth
def notification_settings_api():
    """Which alerts push, and quiet hours (#82). POST takes any subset of the GET fields."""
    if request.method == "GET":
        return jsonify(notification_settings())
    data = json_body()
    updates = {}
    for k in NOTIFY_KINDS:
        if k in data:
            if not isinstance(data[k], bool):
                return jsonify({"error": f'"{k}" must be true/false'}), 400
            updates[f"notify:{k}"] = "1" if data[k] else "0"
    if "quiet_hours" in data:
        q = data["quiet_hours"]
        if not isinstance(q, dict):
            return jsonify({"error": '"quiet_hours" must be an object'}), 400
        if "enabled" in q:
            if not isinstance(q["enabled"], bool):
                return jsonify({"error": '"quiet_hours.enabled" must be true/false'}), 400
            updates["quiet_hours"] = "1" if q["enabled"] else "0"
        for f in ("start", "end"):
            if f in q:
                if not isinstance(q[f], str) or not HHMM.match(q[f]):
                    return jsonify({"error": f'"quiet_hours.{f}" must be "HH:MM"'}), 400
                updates[f"quiet_{f}"] = q[f]
    # Everything is validated before anything is written
    for key, value in updates.items():
        set_state(key, value)
    if updates:
        log_event("system", "NOTIFICATION_SETTINGS",
                  ", ".join(f"{k}={v}" for k, v in updates.items()))
    return jsonify(notification_settings())


@bp.route("/api/notification_history")
@require_auth
def notification_history():
    """The last 20 pushes that went out (#289): events alert() logged as "sent, <detail>" (#296).
    With ?muted=1 also the ones it muted, "muted (<why>), <detail>" (#321). Failed ones and the
    non-push events (occupancy changes, restarts, "no push" ends, ...) aren't listed."""
    muted = request.args.get("muted") == "1"
    where = "value LIKE 'sent, %'" + (" OR value LIKE 'muted (%), %'" if muted else "")
    with db() as conn:
        rows = conn.execute(f"""
            SELECT ts, type, device, value
            FROM events
            WHERE {where}
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

    events = []
    for ts, event_type, device, value in rows:   # newest first
        m = MUTED.match(value)
        events.append({
            "ts": iso(ts),
            "type": event_type,
            "device": device,
            "status": "muted" if m else "sent",
            "muted_reason": m.group(1) if m else None,   # "off" / "quiet hours"
            "detail": m.group(2) if m else value.removeprefix("sent, "),
        })

    return jsonify({"events": events})


MUTED = re.compile(r"muted \((.+?)\), (.*)", re.S)
