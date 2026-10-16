"""REST API (Flask app and routes, contracts/api.md).
"""

import hmac
import logging
import sqlite3
import time
from werkzeug.exceptions import HTTPException
from functools import wraps
from flask import Flask, jsonify, request, Response, abort
from datetime import datetime
from firebase_admin import messaging

import state
from config import (
    AUTH_PASS, AUTH_USER, HEALTH_MAX_MESSAGE_AGE, HEARTBEAT_TIMEOUT, HHMM, HOURLY_MAX_DAYS,
    NOTIFY_KINDS,
)
from store import (
    calculate_combined_confidence, calculate_confidence, db, get_combined_occupancy,
    get_state, log_event, occupancy_stats, set_state, utcnow,
)
from alerts import notification_settings
from ingest import hourly_occupancy_today

log = logging.getLogger(__name__)


def credentials_ok(user, password):
    """Constant-time check of both values, always both, so the timing doesn't tell
    whether the user name or the password was wrong (#61)."""
    user_ok = hmac.compare_digest((user or "").encode(), AUTH_USER.encode())
    pass_ok = hmac.compare_digest((password or "").encode(), AUTH_PASS.encode())
    return user_ok & pass_ok


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not credentials_ok(auth.username, auth.password):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Login Required"'}
            )
        return f(*args, **kwargs)
    return decorated


app = Flask(__name__)


@app.errorhandler(HTTPException)
def http_error(e):
    """API errors as JSON ({"error": ...}) instead of Flask's HTML pages."""
    return jsonify({"error": e.description}), e.code


@app.errorhandler(Exception)
def server_error(e):
    log.exception("%s: %r", request.path, e)
    return jsonify({"error": "internal error"}), 500


def iso(ts):
    """Stored "YYYY-MM-DD HH:MM:SS" (UTC) -> ISO 8601 for the API: "YYYY-MM-DDTHH:MM:SSZ"."""
    return None if ts is None else ts.replace(" ", "T") + "Z"


def json_body():
    """The request's JSON object, or a 400 if the body is missing or not a JSON object."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="JSON object body required")
    return data


# -------------------------
# API ROUTES
# -------------------------

@app.route("/api/health")
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


@app.route("/api/latest")
@require_auth
def latest():
    """Latest temp/humidity + combined radar occupancy (widget).

    Temperature and humidity come from the newest reading of whichever device has them,
    like /api/air: during the node merge the BME280 moved boards (#57).
    """
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT ts, temp, humidity, uptime, device
            FROM readings
            WHERE temp IS NOT NULL OR humidity IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """)
        row = c.fetchone() or (None, None, None, None, None)
        c.execute("SELECT last_seen FROM devices WHERE device = ?", (row[4],))
        dev = c.fetchone()

    online = False
    if dev:
        last_seen = datetime.strptime(dev[0], "%Y-%m-%d %H:%M:%S")
        online = (utcnow() - last_seen).total_seconds() < HEARTBEAT_TIMEOUT

    return jsonify({
        "ts": iso(row[0]),
        "temp": row[1],
        "humidity": row[2],
        "uptime": row[3],
        "online": online,
        "occupied": get_combined_occupancy(),
        "confidence": calculate_combined_confidence(),
    })


@app.route("/api/latest_all")
@require_auth
def latest_all():
    """Latest readings from every device — env nodes and radar nodes."""
    with db() as conn:
        c = conn.cursor()

        now = utcnow()

        def is_online(device):
            c.execute("SELECT last_seen FROM devices WHERE device = ?", (device,))
            dev = c.fetchone()
            if not dev:
                return False
            last_seen = datetime.strptime(dev[0], "%Y-%m-%d %H:%M:%S")
            return (now - last_seen).total_seconds() < HEARTBEAT_TIMEOUT

        result = {}

        # ---- Environmental readings ----
        # A node can send env and CO2 in separate messages (since #57 the CO2 board has
        # the BME280 too), so each comes from its own newest row, with its own timestamp (#11)
        def newest(device, cols, where):
            c.execute(f"SELECT ts, {cols} FROM readings WHERE device = ? AND ({where}) "
                      "ORDER BY id DESC LIMIT 1", (device,))
            return c.fetchone()

        c.execute("SELECT DISTINCT device FROM readings")
        for device in [row[0] for row in c.fetchall()]:
            last = newest(device, "uptime", "1")
            if not last:
                continue
            env = newest(device, "temp, humidity, pressure",
                         "temp IS NOT NULL OR humidity IS NOT NULL OR pressure IS NOT NULL")
            co2 = newest(device, "co2, co2_ok", "co2 IS NOT NULL OR co2_ok IS NOT NULL")

            result[device] = {
                "ts": iso(last[0]),
                "uptime": last[1],
                "online": is_online(device),
                "temp": env[1] if env else None,
                "humidity": env[2] if env else None,
                "pressure": env[3] if env else None,
                "env_ts": iso(env[0]) if env else None,
                "co2": co2[1] if co2 else None,
                "co2_ok": None if not co2 or co2[2] is None else bool(co2[2]),
                "co2_ts": iso(co2[0]) if co2 else None,
            }

        # ---- Radar nodes: merge in, or add if they have no env readings ----
        c.execute("SELECT DISTINCT device FROM presence_readings")
        for device in [row[0] for row in c.fetchall()]:
            c.execute("""
                SELECT ts, occupied, presence, stationary, moving,
                       distance, signal, rssi, uptime, radar_connected
                FROM presence_readings
                WHERE device = ?
                ORDER BY id DESC LIMIT 1
            """, (device,))
            p = c.fetchone()
            if not p:
                continue

            entry = result.get(device, {
                "temp": None,
                "humidity": None,
                "co2": None,
                "pressure": None,
            })

            entry.update({
                "ts": iso(p[0]),
                "uptime": p[8],
                "occupied": p[1],
                "confidence": calculate_confidence(device),
                "online": is_online(device),
                "presence": p[2],
                "stationary": p[3],
                "moving": p[4],
                "distance": p[5],
                "signal": p[6],
                "rssi": p[7],
                "radar_connected": p[9],
            })
            result[device] = entry

    result["combined"] = {
        "occupied": get_combined_occupancy(),
        "confidence": calculate_combined_confidence()
    }

    return jsonify(result)


@app.route("/api/restarts")
@require_auth
def restarts():
    """Restart counts per device, last 24h and 7d, plus the most recent."""
    with db() as conn:
        c = conn.cursor()

        c.execute("""
            SELECT device, COUNT(*)
            FROM events
            WHERE type = 'RESTART'
              AND ts >= datetime('now','-24 hours')
            GROUP BY device
        """)
        day = dict(c.fetchall())

        c.execute("""
            SELECT device, COUNT(*)
            FROM events
            WHERE type = 'RESTART'
              AND ts >= datetime('now','-7 days')
            GROUP BY device
        """)
        week = dict(c.fetchall())

        c.execute("""
            SELECT device, MAX(ts)
            FROM events
            WHERE type = 'RESTART'
            GROUP BY device
        """)
        last = dict(c.fetchall())

    devices = set(day) | set(week) | set(last)
    return jsonify({
        d: {
            "last_24h": day.get(d, 0),
            "last_7d": week.get(d, 0),
            "last_restart": iso(last.get(d)),
        } for d in devices
    })


@app.route("/api/hourly")
@require_auth
def hourly():
    # Temperature/humidity from one device if ?device= is given, else from whichever
    # device reports them (the BME280 moved boards in the node merge, #57)
    device = request.args.get("device")
    # days=1: today; days=N: today and the N-1 days before it (history screen, #82).
    # Readings are kept for 8 days, so 7 full days is the most there is.
    days = request.args.get("days", "1")
    if not days.isdigit() or not 1 <= int(days) <= HOURLY_MAX_DAYS:
        return jsonify({"error": f"days must be 1-{HOURLY_MAX_DAYS}"}), 400
    days = int(days)
    since = f"-{days - 1} days"

    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT
                strftime('%Y-%m-%d %H:00', ts, 'localtime'),
                AVG(temp),
                AVG(humidity)
            FROM readings
            WHERE (? IS NULL OR device = ?) AND (temp IS NOT NULL OR humidity IS NOT NULL)
              AND ts >= datetime('now','localtime','start of day',?,'utc')
            GROUP BY 1
        """, (device, device, since))
        env_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}
        # CO2 and pressure from whichever device reports them (like /api/air)
        c.execute("""
            SELECT
                strftime('%Y-%m-%d %H:00', ts, 'localtime'),
                AVG(co2),
                AVG(pressure)
            FROM readings
            WHERE (co2 IS NOT NULL OR pressure IS NOT NULL)
              AND ts >= datetime('now','localtime','start of day',?,'utc')
            GROUP BY 1
        """, (since,))
        air_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}
    occ_rows = hourly_occupancy_today(days)

    hours = sorted(set(env_rows) | set(air_rows) | set(occ_rows), reverse=True)
    out = []
    for h in hours:
        t, hu = env_rows.get(h, (None, None))
        co2, pr = air_rows.get(h, (None, None))
        o = occ_rows.get(h)
        out.append({
            "hour": h,
            "avg_temp": round(t, 1) if t is not None else None,
            "avg_humidity": round(hu, 1) if hu is not None else None,
            "avg_co2": round(co2) if co2 is not None else None,
            "avg_pressure": round(pr, 1) if pr is not None else None,
            "avg_occupancy": round(o, 0) if o is not None else None,
        })
    return jsonify(out)


@app.route("/api/air")
@require_auth
def air():
    """Latest CO2 + pressure, with a simple quality band."""
    with db() as conn:
        c = conn.cursor()

        # CO2 — whichever device reports it
        c.execute("""
            SELECT ts, co2 FROM readings
            WHERE co2 IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """)
        co2_row = c.fetchone()

        # Pressure + env — whichever device reports it
        c.execute("""
            SELECT ts, pressure, temp, humidity FROM readings
            WHERE pressure IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """)
        env_row = c.fetchone()

        # Pressure as it was 3 hours ago
        c.execute("""
            SELECT pressure FROM readings
            WHERE pressure IS NOT NULL
              AND ts <= datetime('now','-3 hours')
            ORDER BY id DESC LIMIT 1
        """)
        old = c.fetchone()

    co2 = co2_row[1] if co2_row else None
    if co2 is None:        band = None
    elif co2 < 800:        band = "good"
    elif co2 < 1200:       band = "fair"
    elif co2 < 2000:       band = "stuffy"
    else:                  band = "bad"

    pressure = env_row[1] if env_row else None
    trend = None
    if old and pressure is not None:
        trend = round(pressure - old[0], 1)

    return jsonify({
        "ts": iso(env_row[0] if env_row else (co2_row[0] if co2_row else None)),
        "co2": co2,
        "band": band,
        "pressure": pressure,
        "pressure_3h_change": trend,
        "temp": env_row[2] if env_row else None,
        "humidity": env_row[3] if env_row else None,
    })


@app.route("/api/devices")
@require_auth
def devices():
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT device, last_seen FROM devices")
        rows = c.fetchall()

    now = utcnow()
    out = []

    for dev, ts in rows:
        last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        out.append({
            "device": dev,
            "last_seen": iso(ts),
            "online": (now - last).total_seconds() < HEARTBEAT_TIMEOUT
        })

    return jsonify(out)


@app.route("/api/register_token", methods=["POST"])
@require_auth
def register_token():
    data = json_body()
    token = data.get("token")

    if not token:
        return jsonify({"error": "No token provided"}), 400

    # Phones aren't sensor nodes, so no touch_device() here (#15)
    messaging.subscribe_to_topic([token], "env")

    return jsonify({"success": True})


@app.route("/api/occupancy_arm", methods=["POST"])
@require_auth
def occupancy_arm():
    data = json_body()
    if not isinstance(data.get("armed"), bool):
        return jsonify({"error": '"armed" (true/false) required'}), 400
    armed = "1" if data["armed"] else "0"
    set_state("occupancy_armed", armed)
    log_event("system", "OCCUPANCY_ARMED", armed)
    return jsonify({"armed": armed == "1"})


@app.route("/api/occupancy_status")
@require_auth
def occupancy_status():
    return jsonify({"armed": get_state("occupancy_armed") == "1"})


@app.route("/api/notification_settings", methods=["GET", "POST"])
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


@app.route("/api/stats24h_combined")
@require_auth
def stats24h_combined():
    '''Combined 24h env stats from ALL devices + radar occupancy'''
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT temp, humidity
            FROM readings
            WHERE ts >= datetime('now','-24 hours')
              AND temp IS NOT NULL
              AND humidity IS NOT NULL
        """)
        rows = c.fetchall()

    occ = occupancy_stats(24)
    temps = [r[0] for r in rows]
    hums = [r[1] for r in rows]

    return jsonify({
        "avg_temp": round(sum(temps)/len(temps), 1) if rows else None,
        "avg_humidity": round(sum(hums)/len(hums), 1) if rows else None,
        "avg_occupancy": occ["avg_occupancy"],
        "occupied_hours": occ["occupied_hours"],
        "coverage_hours": occ["coverage_hours"]
    })
