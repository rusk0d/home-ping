import json
import sqlite3
import os
import re
from flask import Flask, jsonify, request, Response, abort
from werkzeug.exceptions import HTTPException
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from functools import wraps
from dotenv import load_dotenv
import time
import threading


import firebase_admin
from firebase_admin import credentials, messaging

# -------------------------
# ENV + AUTH
# -------------------------
load_dotenv()

AUTH_USER = os.environ["DASH_USER"]
AUTH_PASS = os.environ["DASH_PASS"]



def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != AUTH_USER or auth.password != AUTH_PASS:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Login Required"'}
            )
        return f(*args, **kwargs)
    return decorated

# -------------------------
# CONFIG
# -------------------------
DB = "data.db"

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASS = os.environ["MQTT_PASS"]
TOPIC = "home/esp32/#"  # Subscribe to all topics under home/esp32/
EVENTS_RETENTION_DAYS = 30    # events older than this are deleted
DEVICES_RETENTION_DAYS = 30   # devices not seen for this long are removed from the list
NODE_OFFLINE_ALERT_SECONDS = 300  # no data this long -> "node offline" push (longer than
                                  # HEARTBEAT_TIMEOUT so a reboot or WiFi blip doesn't alert)
NODE_CHECK_INTERVAL = 30          # seconds between offline checks
HEARTBEAT_TIMEOUT = 120  # seconds without a message before a device counts as offline
FIREBASE_CRED = "firebase_service_account.json"
LAST_UPTIME = {}
UPTIME_DROP_TOLERANCE = 120   # seconds; a restart needs uptime to drop by more (see note_uptime)
# Presence (LD2410C radar) — the ONLY source of occupancy data.
# PIR "occupied" fields from other devices are ignored.
PRESENCE_DEVICE = "home_ping_esp32"
HOURLY_MAX_DAYS = 7            # /api/hourly?days= limit (readings are kept 8 days)
HEALTH_MAX_MESSAGE_AGE = 300   # /api/health fails when no live MQTT message came for this long (#77)
PRESENCE_SIGNAL_THRESHOLD = 15   # stationary readings below this energy are treated as noise
# Hourly occupancy for the web page (outside home/esp32/# so we don't receive our own messages)
OCCUPANCY_HOURLY_TOPIC = "home/backend/occupancy_hourly"
OCCUPANCY_HOURLY_INTERVAL = 300  # seconds

app = Flask(__name__)


@app.errorhandler(HTTPException)
def http_error(e):
    """API errors as JSON ({"error": ...}) instead of Flask's HTML pages."""
    return jsonify({"error": e.description}), e.code


@app.errorhandler(Exception)
def server_error(e):
    print(f"[ERROR] {request.path}: {e!r}")
    return jsonify({"error": "internal error"}), 500


def utcnow():
    """Naive UTC now: comparable with the stored timestamps, which are UTC (#65)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
# FIREBASE INIT
# -------------------------
try:
    firebase_admin.get_app()   # already initialised (e.g. module imported twice)
except ValueError:
    firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED))

# -------------------------
# DATABASE INIT
# -------------------------
READINGS_COLUMNS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    ts TIMESTAMP,
    temp REAL,
    humidity REAL,
    uptime INTEGER,
    co2 INTEGER,
    pressure REAL,
    co2_ok INTEGER
"""


def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute(f"CREATE TABLE IF NOT EXISTS readings ({READINGS_COLUMNS})")

    # Add columns missing from older databases (including ones created by the
    # removed init_db.py, which had only id/ts/temp/humidity)
    for col, coltype in (("device", "TEXT"), ("uptime", "INTEGER"),
                         ("co2", "INTEGER"), ("pressure", "REAL"), ("co2_ok", "INTEGER")):
        try:
            c.execute(f"ALTER TABLE readings ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Drop columns that were always 0 (#14). Rebuild the table instead of
    # ALTER TABLE ... DROP COLUMN, which needs SQLite 3.35+.
    cols = {r[1] for r in c.execute("PRAGMA table_info(readings)")}
    if cols & {"occupied", "occupancy_confidence", "dark"}:
        keep = "id, device, ts, temp, humidity, uptime, co2, pressure, co2_ok"
        conn.commit()
        c.executescript(f"""
            BEGIN;
            CREATE TABLE readings_new ({READINGS_COLUMNS});
            INSERT INTO readings_new ({keep}) SELECT {keep} FROM readings;
            DROP TABLE readings;
            ALTER TABLE readings_new RENAME TO readings;
            COMMIT;
        """)
        print("[DB] readings: dropped occupied, occupancy_confidence, dark")

    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device TEXT PRIMARY KEY,
            last_seen TIMESTAMP
        )
    """)
    # register_token used to add phones as "unknown"; devices is for sensor nodes only
    c.execute("DELETE FROM devices WHERE device = 'unknown'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            ts TIMESTAMP,
            type TEXT,
            value TEXT
        )
    """)

    c.execute("""
        INSERT OR IGNORE INTO system_state (key, value)
        VALUES ('occupancy_armed', '0')
    """)
    # Soil moisture feature removed (#9): no sensor exists, drop its unused table
    c.execute("DROP TABLE IF EXISTS soil_readings")

    # Presence readings from the LD2410C radar node
    c.execute("""
        CREATE TABLE IF NOT EXISTS presence_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            ts TIMESTAMP,
            occupied INTEGER,
            presence INTEGER,
            stationary INTEGER,
            moving INTEGER,
            distance INTEGER,
            signal INTEGER,
            rssi INTEGER,
            uptime INTEGER,
            radar_connected INTEGER
        )
    """)

    # Timestamps were stored in server local time until #65; convert existing rows to
    # UTC once. SQLite's 'utc' modifier applies the DST offset of each row's own date.
    if c.execute("SELECT 1 FROM system_state WHERE key = 'ts_utc'").fetchone() is None:
        for table, col in (("readings", "ts"), ("presence_readings", "ts"),
                           ("events", "ts"), ("devices", "last_seen")):
            c.execute(f"UPDATE {table} SET {col} = datetime({col}, 'utc') WHERE {col} IS NOT NULL")
        c.execute("INSERT INTO system_state (key, value) VALUES ('ts_utc', '1')")
        conn.commit()
        print("[DB] timestamps converted to UTC")

    # Indexes for the per-device lookups (#18). (device, id) serves "latest row of a
    # device"; (device, ts) serves time windows. No plain ts index: it made SQLite pick
    # worse plans for several endpoints (measured on an 8-day, 276k-row database).
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_presence_device_id ON presence_readings(device, id)",
        "CREATE INDEX IF NOT EXISTS ix_presence_device_ts ON presence_readings(device, ts)",
        "CREATE INDEX IF NOT EXISTS ix_readings_device_id ON readings(device, id)",
        "CREATE INDEX IF NOT EXISTS ix_readings_device_ts ON readings(device, ts)",
        "CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts)",
    ):
        c.execute(sql)
    conn.commit()
    # Without statistics SQLite picks the wrong index for several queries
    c.execute("ANALYZE")
    conn.commit()
    conn.close()

init_db()

# -------------------------
# DATABASE HELPERS
# -------------------------
def touch_device(device):
    """Update device heartbeat"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO devices (device, last_seen)
        VALUES (?, datetime('now'))
        ON CONFLICT(device)
        DO UPDATE SET last_seen=datetime('now')
    """, (device,))
    # Forget nodes that have been gone for a long time (e.g. a retired node)
    c.execute("DELETE FROM devices WHERE last_seen < datetime('now',?)",
              (f"-{DEVICES_RETENTION_DAYS} days",))
    conn.commit()
    conn.close()
    if device in NODE_OFFLINE:
        node_back_online(device)

def log_to_db(device, temp, hum, uptime, co2=None, pressure=None, co2_ok=None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
        INSERT INTO readings
        (device, ts, temp, humidity, uptime, co2, pressure, co2_ok)
        VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)
    """, (device, temp, hum, uptime, co2, pressure, co2_ok))

    c.execute("""
        DELETE FROM readings
        WHERE ts < datetime('now','-8 days')
    """)

    conn.commit()
    conn.close()

def log_presence(device, occupied, presence, stationary, moving,
                 distance, signal, rssi, uptime, radar_connected):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO presence_readings
        (device, ts, occupied, presence, stationary, moving,
         distance, signal, rssi, uptime, radar_connected)
        VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (device, occupied, presence, stationary, moving,
          distance, signal, rssi, uptime, radar_connected))

    # Keep 8 days so the 24h queries always have full coverage
    c.execute("""
        DELETE FROM presence_readings
        WHERE ts < datetime('now','-8 days')
    """)
    conn.commit()
    conn.close()
def occupancy_stats(hours=24):
    """Occupancy over a window, using each radar's real sample spacing."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT device,
               COUNT(*),
               SUM(occupied),
               MIN(ts),
               MAX(ts)
        FROM presence_readings
        WHERE ts >= datetime('now', ?)
        GROUP BY device
    """, (f"-{hours} hours",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}

    best_pct = 0
    best_hours = 0
    coverage = 0

    for device, total, occupied, first_ts, last_ts in rows:
        if not total:
            continue
        pct = (occupied / total) * 100

        # Real span this device actually covered
        t0 = datetime.strptime(first_ts, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S")
        span_h = max((t1 - t0).total_seconds() / 3600, 0)

        if pct > best_pct:
            best_pct = pct
            best_hours = span_h * (occupied / total)
        coverage = max(coverage, span_h)

    return {
        "avg_occupancy": round(best_pct, 0),
        "occupied_hours": round(best_hours, 2),
        "coverage_hours": round(coverage, 1),
    }

# "confidence" everywhere in the API: % of a radar's rows in the last
# CONFIDENCE_MINUTES that were occupied (0-100), or None without radar data.
CONFIDENCE_MINUTES = 15


def calculate_confidence(device, minutes=CONFIDENCE_MINUTES):
    """Occupancy confidence for one radar over a time window (0-100), None without data."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT occupied FROM presence_readings
        WHERE device = ? AND ts >= datetime('now', ?)
    """, (device, f"-{minutes} minutes"))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return None

    return int((sum(r[0] for r in rows) / len(rows)) * 100)

def get_combined_occupancy():
    """1 if any radar has 2+ occupied rows in its last 3 reports (last 2 min), 0 if not,
    None if no radar reported in the last 2 minutes."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT DISTINCT device FROM presence_readings")
    devices = [r[0] for r in c.fetchall()]

    result = None
    for d in devices:
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ? AND ts >= datetime('now','-2 minutes')
            ORDER BY id DESC LIMIT 3
        """, (d,))
        rows = [r[0] for r in c.fetchall()]
        if rows and result is None:
            result = 0
        if sum(rows) >= 2:
            result = 1
    conn.close()
    return result


def calculate_combined_confidence(minutes=CONFIDENCE_MINUTES):
    """Per-radar confidence over the window, then the highest of them; None without data."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT device, AVG(occupied)
        FROM presence_readings
        WHERE ts >= datetime('now', ?)
        GROUP BY device
    """, (f"-{minutes} minutes",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return None
    return int(max(r[1] for r in rows) * 100)

def get_last_occupancy(device):
    """Previous (second most recent) radar occupancy state."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT occupied FROM presence_readings
        WHERE device = ?
        ORDER BY id DESC LIMIT 1 OFFSET 1
    """, (device,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def log_event(device, event_type, value):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO events (device, ts, type, value)
        VALUES (?, datetime('now'), ?, ?)
    """, (device, event_type, value))
    c.execute("DELETE FROM events WHERE ts < datetime('now',?)",
              (f"-{EVENTS_RETENTION_DAYS} days",))
    conn.commit()
    conn.close()

def get_state(key, default="0"):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT value FROM system_state WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_state(key, value):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO system_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()
    conn.close()
def note_uptime(device, uptime):
    """Log a restart when a device's uptime goes backwards."""
    if uptime is None:
        return
    prev = LAST_UPTIME.get(device)
    LAST_UPTIME[device] = uptime

    # First message since the backend started — no baseline to compare
    if prev is None:
        return

    # A real reboot resets uptime to near zero. Require a meaningful drop, not just
    # a smaller number: a node that publishes on several topics (env, presence) can
    # send them slightly out of order, so its uptime may briefly go back a little.
    if uptime < prev - UPTIME_DROP_TOLERANCE:
        log_event(device, "RESTART", f"uptime {prev}s -> {uptime}s")
        print(f"[RESTART] {device}: was up {prev}s, now {uptime}s")
# -------------------------
# PUSH NOTIFICATIONS (FCM)
# -------------------------
def send_push(title, body, data=None):
    """Send a data-only push to topic "env". Returns True if FCM accepted it.

    Data-only so the Android app always builds the notification itself (its own
    channels and IDs), also in the background; title/body travel in the data map.
    High priority so it is delivered while the phone is in Doze.
    """
    print(f"[DEBUG] Preparing to send push: title={title}, body={body}, data={data}")
    try:
        message = messaging.Message(
            data={"title": title, "body": body, **(data or {})},
            android=messaging.AndroidConfig(priority="high"),
            topic="env"
        )
        response = messaging.send(message)
        print(f"[DEBUG] Push sent! Response: {response}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send push: {e}")
        return False


def alert(device, event_type, detail, title, body, data, cooldown_key=None, kind=None):
    """Send a push and record it in events as "sent, ...", "failed, ..." or "muted (...), ...".

    The cooldown only starts after a successful send; a failed one is retried
    after PUSH_RETRY_SECONDS instead of being silenced for the whole cooldown.
    [kind] (one of NOTIFY_KINDS) can be switched off or muted by quiet hours (#82);
    a muted alert is handled like a sent one (cooldown, once per outage) without a push.
    Returns "sent", "muted", or False when the send failed.
    """
    muted = alert_muted(kind)
    if muted:
        log_event(device, event_type, f"muted ({muted}), {detail}")
        if cooldown_key:
            set_cooldown(cooldown_key, time.time())
        return "muted"
    ok = send_push(title, body, data)
    status = "sent" if ok else "failed"
    log_event(device, event_type, f"{status}, {detail}")
    if cooldown_key:
        now = time.time()
        set_cooldown(cooldown_key, now if ok else now - NOTIFICATION_COOLDOWN + PUSH_RETRY_SECONDS)
    return "sent" if ok else False


# -------------------------
# NOTIFICATION SETTINGS (#82)
# -------------------------
# system_state: notify:<kind> = '1'/'0' (missing = on), quiet_hours = '1'/'0',
# quiet_start / quiet_end = "HH:MM" in the server's local time (Budapest).
# Occupancy alerts have their own switch (occupancy_armed) and ignore quiet hours.
NOTIFY_KINDS = ("humidity", "temperature", "radar", "nodes")
QUIET_DEFAULTS = {"start": "22:00", "end": "07:00"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def notification_settings():
    return {
        **{k: get_state(f"notify:{k}", "1") == "1" for k in NOTIFY_KINDS},
        "quiet_hours": {
            "enabled": get_state("quiet_hours", "0") == "1",
            "start": get_state("quiet_start", QUIET_DEFAULTS["start"]),
            "end": get_state("quiet_end", QUIET_DEFAULTS["end"]),
        },
    }


def in_quiet_hours(now=None):
    """True inside the quiet-hours window; it may cross midnight (22:00-07:00)."""
    if get_state("quiet_hours", "0") != "1":
        return False
    start = get_state("quiet_start", QUIET_DEFAULTS["start"])
    end = get_state("quiet_end", QUIET_DEFAULTS["end"])
    t = (now or datetime.now()).strftime("%H:%M")   # local time, like the stored window
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def alert_muted(kind):
    """Why an alert of [kind] gets no push ("off", "quiet hours"), or None."""
    if kind is None:
        return None
    if get_state(f"notify:{kind}", "1") != "1":
        return "off"
    if in_quiet_hours():
        return "quiet hours"
    return None
QUIET_MINUTES_BEFORE_ALERT = 60   # flat must be quiet this long before an arrival alerts
ARRIVAL_CONFIRM_SECONDS = 120     # movement this soon after the quiet spell ends confirms the arrival


def any_radar_quiet_minutes():
    """Minutes since ANY radar last reported occupancy (call before logging the current row)."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts FROM presence_readings
        WHERE occupied = 1
        ORDER BY id DESC LIMIT 1
    """)
    row = c.fetchone()
    conn.close()

    if not row:
        return 9999
    last_occ = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    return (utcnow() - last_occ).total_seconds() / 60


def handle_presence_data(data):
    """Handle LD2410C radar presence data — the only occupancy source."""
    device = data.get("device", PRESENCE_DEVICE)
    presence = 1 if data.get("presence", False) else 0
    stationary = 1 if data.get("stationary", False) else 0
    moving = 1 if data.get("moving", False) else 0
    distance = int(data.get("distance", 0) or 0)
    signal = int(data.get("signal", 0) or 0)
    rssi = int(data.get("rssi", 0) or 0)
    uptime = int(data.get("uptime", 0) or 0)
    radar_connected = 1 if data.get("radar_connected", True) else 0

    # Filter noise: moving always counts, stationary only with a decent signal
    occupied = 1 if (moving or (stationary and signal >= PRESENCE_SIGNAL_THRESHOLD)) else 0

    # How long has the whole flat been quiet? Read BEFORE logging this row.
    quiet_minutes = any_radar_quiet_minutes()

    log_presence(device, occupied, presence, stationary, moving,
                 distance, signal, rssi, uptime, radar_connected)
    touch_device(device)
    note_uptime(device, uptime)

    prev_occ = get_last_occupancy(device)
    if prev_occ is not None and prev_occ != occupied:
        log_event(device, "OCCUPANCY_CHANGE", "occupied" if occupied else "vacant")

    now = time.time()

    # -------------------------
    # RADAR OFFLINE — only after it has been gone for a while,
    # so a single eink-refresh blip does not notify
    # -------------------------
    if not radar_connected:
        RADAR_FAIL_COUNT[device] = RADAR_FAIL_COUNT.get(device, 0) + 1
    else:
        RADAR_FAIL_COUNT[device] = 0
        if device in RADAR_ALERTED:  # back: the next outage alerts again
            set_radar_alerted(device, False)

    # Once per outage (not every cooldown while it stays down)
    last_radar_alert = LAST_NOTIFICATION.get(f"RADAR_{device}", 0)
    if (RADAR_FAIL_COUNT.get(device, 0) >= 12
            and device not in RADAR_ALERTED
            and now - last_radar_alert > NOTIFICATION_COOLDOWN):
        if alert(device, "RADAR_DISCONNECTED", "no frames from LD2410C",
                 title="Radar Disconnected",
                 body=f"{device}: LD2410C not responding",
                 data={"event": "RADAR_DISCONNECTED", "device": device},
                 cooldown_key=f"RADAR_{device}", kind="radar"):
            set_radar_alerted(device, True)

    # -------------------------
    # OCCUPANCY ALERT — fires once per arrival, after a long quiet spell
    # -------------------------
    # The first occupied reading after the quiet spell starts an arrival; it is often
    # stationary-only (the LD2410 sees a still target first), and it already ends the
    # quiet spell. So movement from any radar within ARRIVAL_CONFIRM_SECONDS confirms it.
    # A stationary ghost reading never moves and lets the window expire.
    if occupied and quiet_minutes >= QUIET_MINUTES_BEFORE_ALERT:
        set_state("arrival_started", str(now))
        set_state("arrival_quiet", str(int(quiet_minutes)))
    if occupied and moving and now - float(get_state("arrival_started")) <= ARRIVAL_CONFIRM_SECONDS:
        set_state("arrival_started", "0")   # once per arrival
        if get_state("occupancy_armed") == "1":
            quiet = get_state("arrival_quiet")
            where = "hallway" if "hallway" in device else "main room"
            alert(device, "OCCUPANCY_ALERT", f"after {quiet} min quiet",
                  title="Occupancy Alert",
                  body=f"Movement in the {where} after {quiet} min of quiet ({distance}cm)",
                  data={"event": "OCCUPIED", "device": device, "distance": str(distance)})

    print(f"[PRESENCE] {device}: occupied={occupied} moving={moving} "
          f"stationary={stationary} dist={distance}cm signal={signal} "
          f"rssi={rssi} quiet={int(quiet_minutes)}min")

# -------------------------
# MQTT CALLBACK
# -------------------------
# Alert state that must survive a backend restart (#5) lives in system_state:
#   cooldown:<key>       = unix time the cooldown for <key> started
#   radar_alerted:<dev>  = '1' while that radar's current outage has been pushed
def load_alert_state():
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT key, value FROM system_state
        WHERE key LIKE 'cooldown:%' OR key LIKE 'radar_alerted:%' OR key LIKE 'node_offline:%'
    """).fetchall()
    conn.close()
    cooldowns = {k[len("cooldown:"):]: float(v) for k, v in rows if k.startswith("cooldown:")}
    alerted = {k[len("radar_alerted:"):] for k, v in rows if k.startswith("radar_alerted:") and v == "1"}
    # node_offline: '1' = offline push sent, 'm' = offline but muted (#82), '0' = online
    offline = {k[len("node_offline:"):]: v == "1" for k, v in rows
               if k.startswith("node_offline:") and v in ("1", "m")}
    return cooldowns, alerted, offline


def set_cooldown(key, started):
    LAST_NOTIFICATION[key] = started
    set_state(f"cooldown:{key}", repr(started))


def set_radar_alerted(device, alerted):
    if alerted:
        RADAR_ALERTED.add(device)
    else:
        RADAR_ALERTED.discard(device)
    set_state(f"radar_alerted:{device}", "1" if alerted else "0")


LAST_NOTIFICATION, RADAR_ALERTED, NODE_OFFLINE = load_alert_state()
NODE_LOCK = threading.Lock()  # offline checks (own thread) vs. messages (MQTT thread)
STARTED_AT = time.time()


# -------------------------
# NODE OFFLINE / BACK ONLINE (#13)
# -------------------------
# Decided on data, not on the MQTT last will: the nodes reconnect with a fixed
# client ID, so after a short WiFi drop the broker publishes the old session's
# "offline" just as the node comes back. A node is offline when it hasn't sent
# anything for NODE_OFFLINE_ALERT_SECONDS. One push per outage, one when it's
# back; both use event NODE_STATUS so the "back" push replaces the "offline" one
# in the app (same notification slot).
def check_nodes():
    # Right after a backend restart every last_seen is old: give the nodes time to report
    if time.time() - STARTED_AT < NODE_OFFLINE_ALERT_SECONDS:
        return
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT device, last_seen FROM devices").fetchall()
    conn.close()
    now = utcnow()
    for device, last_seen in rows:
        age = (now - datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")).total_seconds()
        if age < NODE_OFFLINE_ALERT_SECONDS:
            continue
        with NODE_LOCK:
            if device in NODE_OFFLINE:
                continue
            if time.time() - LAST_NOTIFICATION.get(f"NODE_{device}", 0) <= NOTIFICATION_COOLDOWN:
                continue
            result = alert(device, "NODE_OFFLINE", f"last seen {last_seen}",
                           title="Node offline",
                           body=f"{device}: no data for {int(age // 60)} min",
                           data={"event": "NODE_STATUS", "status": "offline", "device": device},
                           cooldown_key=f"NODE_{device}", kind="nodes")
            if result:
                set_node_offline(device, True, pushed=result == "sent")


def node_back_online(device):
    with NODE_LOCK:
        if device not in NODE_OFFLINE:
            return
        pushed = NODE_OFFLINE[device]
        set_node_offline(device, False)
        if not pushed:   # the offline alert was muted: nothing to clear on the phone
            log_event(device, "NODE_ONLINE", "no push (offline alert was muted), data again")
            return
        alert(device, "NODE_ONLINE", "data again",
              title="Node back online",
              body=f"{device} is sending data again",
              data={"event": "NODE_STATUS", "status": "online", "device": device},
              kind="nodes")


def set_node_offline(device, offline, pushed=True):
    """[pushed]: whether the offline push was sent (False: muted, #82)."""
    if offline:
        NODE_OFFLINE[device] = pushed
    else:
        NODE_OFFLINE.pop(device, None)
    set_state(f"node_offline:{device}", ("1" if pushed else "m") if offline else "0")


def node_check_loop():
    while True:
        time.sleep(NODE_CHECK_INTERVAL)
        try:
            check_nodes()
        except Exception as e:
            print("[NODES] offline check failed:", e)


def handle_status(device, status):
    """home/esp32/<device>/status: "online" on connect, "offline" as the node's last will."""
    print(f"[STATUS] {device}: {status}")
    log_event(device, "STATUS", status)
    if status == "online":
        touch_device(device)   # a sign of life; "offline" (sent by the broker) is not
LAST_MESSAGE_AT = None  # last live (non-retained) MQTT message, for /api/health (#77)
RADAR_FAIL_COUNT = {}  # consecutive radar_connected=false messages (in memory; 12 needed to alert)
NOTIFICATION_COOLDOWN = 15 * 60  # 15 minutes per alert key
PUSH_RETRY_SECONDS = 60          # retry a failed push after this, not after the full cooldown
TEMP_THRESHOLD = 18
HUMIDITY_HIGH_THRESHOLD = 65
HUMIDITY_LOW_THRESHOLD = 25

def on_message(client, userdata, msg):
    # Nothing may escape this callback: paho re-raises callback exceptions, which
    # ends its network thread and silently stops all ingestion (#58). So the whole
    # body is in the try, and the payload is decoded leniently (bad bytes -> U+FFFD).
    try:
        payload = msg.payload.decode("utf-8", errors="replace")
        print(f"[MQTT] Received message on topic: {msg.topic}")
        print(f"[MQTT] Payload: {payload}")
        # The broker replays the last retained message of each topic when we
        # (re)subscribe, with the retain flag set. That's an old reading: storing it
        # would add a duplicate row with a fresh ts and could re-send alerts (#5).
        # Live messages arrive without the flag. The firmware keeps retaining for
        # the web page, which wants the last value immediately.
        if msg.retain:
            print(f"[MQTT] Skipping retained message on {msg.topic}")
            return
        global LAST_MESSAGE_AT
        LAST_MESSAGE_AT = time.time()

        # home/esp32/<device>/status carries plain "online"/"offline" (#13)
        parts = msg.topic.split("/")
        if len(parts) == 4 and parts[3] == "status":
            handle_status(parts[2], payload.strip())
            return

        data = json.loads(payload)

        if "presence" in data:
            handle_presence_data(data)
            return

        # ---- Everything else: environmental sensors. PIR fields are ignored. ----
        device = data.get("device", "esp32")
        temp = data.get("temp_c")
        hum = data.get("humidity")
        uptime = data.get("uptime", 0)
        co2 = data.get("co2")
        pressure = data.get("pressure")

        # co2_ok is stored with the reading so it survives a backend restart
        co2_ok = (1 if data["co2_ok"] else 0) if "co2_ok" in data else None

        log_to_db(device, temp, hum, uptime, co2, pressure, co2_ok)
        touch_device(device)
        note_uptime(device, uptime)

        now = time.time()

        # Each alert only needs its own value (a node may send just one of them)

        # -------------------------
        # HUMIDITY ALERT
        # -------------------------
        last_hum_sent = LAST_NOTIFICATION.get(f"HUMIDITY_{device}", 0)
        if hum is not None and now - last_hum_sent > NOTIFICATION_COOLDOWN:
            if hum > HUMIDITY_HIGH_THRESHOLD:
                alert(device, "HUMIDITY_HIGH_ALERT", f"humidity={hum}",
                      title="Humidity Alert",
                      body=f"Humidity is high: {hum:.1f}%",
                      data={"event": "HUMIDITY_HIGH", "device": device},
                      cooldown_key=f"HUMIDITY_{device}", kind="humidity")

            elif hum < HUMIDITY_LOW_THRESHOLD:
                alert(device, "HUMIDITY_LOW_ALERT", f"humidity={hum}",
                      title="Humidity Alert",
                      body=f"Humidity is low: {hum:.1f}%",
                      data={"event": "HUMIDITY_LOW", "device": device},
                      cooldown_key=f"HUMIDITY_{device}", kind="humidity")

        # -------------------------
        # TEMPERATURE ALERT
        # -------------------------
        last_temp_sent = LAST_NOTIFICATION.get(f"TEMP_{device}", 0)
        if temp is not None and temp < TEMP_THRESHOLD and (now - last_temp_sent > NOTIFICATION_COOLDOWN):
            alert(device, "TEMP_ALERT", f"temp={temp}",
                  title="Temperature Alert",
                  body=f"Temperature is low on {device}: {temp:.1f}°C",
                  data={"event": "TEMP", "device": device},
                  cooldown_key=f"TEMP_{device}", kind="temperature")

    except Exception as e:
        print("MQTT error:", repr(e), "on", getattr(msg, "topic", "?"))

# -------------------------
# MQTT SETUP
# -------------------------
def hourly_occupancy_today(days=1):
    """{'YYYY-MM-DD HH:00': mean(occupied)*100} for PRESENCE_DEVICE since local midnight
    (days - 1) days ago; days=1 is today."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT
            strftime('%Y-%m-%d %H:00', ts, 'localtime'),
            AVG(occupied)*100
        FROM presence_readings
        WHERE device = ? AND ts >= datetime('now','localtime','start of day',?,'utc')
        GROUP BY 1
    """, (PRESENCE_DEVICE, f"-{days - 1} days"))
    rows = {r[0]: r[1] for r in c.fetchall()}
    conn.close()
    return rows

def publish_hourly_occupancy():
    occ = hourly_occupancy_today()
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    hours = []
    for h in range(now.hour + 1):
        v = occ.get(f"{day} {h:02d}:00")
        hours.append({"hour": f"{h:02d}:00", "occupancy": round(v) if v is not None else None})
    payload = {
        "device": PRESENCE_DEVICE,
        "date": day,
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "hours": hours,
    }
    mqttc.publish(OCCUPANCY_HOURLY_TOPIC, json.dumps(payload), qos=0, retain=True)

def hourly_occupancy_loop():
    while True:
        time.sleep(OCCUPANCY_HOURLY_INTERVAL)
        try:
            publish_hourly_occupancy()
        except Exception as e:
            print("[MQTT] hourly occupancy publish failed:", e)

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected, rc={rc}")
    client.subscribe(TOPIC)
    try:
        publish_hourly_occupancy()
    except Exception as e:
        print("[MQTT] hourly occupancy publish failed:", e)

mqttc = mqtt.Client()
mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
mqttc.on_connect = on_connect
mqttc.on_message = on_message
mqttc.connect(MQTT_HOST, MQTT_PORT)
mqttc.loop_start()

def on_disconnect(client, userdata, rc):
    print(f"[MQTT] Disconnected, rc={rc}")

mqttc.on_disconnect = on_disconnect

threading.Thread(target=hourly_occupancy_loop, daemon=True).start()
threading.Thread(target=node_check_loop, daemon=True).start()
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
    last_age = int(now - LAST_MESSAGE_AT) if LAST_MESSAGE_AT else None
    try:
        conn = sqlite3.connect(DB)
        conn.execute("SELECT 1 FROM system_state LIMIT 1").fetchall()
        conn.close()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    mqtt_ok = mqttc.is_connected()
    fresh = now - (LAST_MESSAGE_AT or STARTED_AT) < HEALTH_MAX_MESSAGE_AGE
    ok = mqtt_ok and fresh and db_ok
    body = {"ok": ok, "mqtt_connected": mqtt_ok, "last_message_age_s": last_age, "db_ok": db_ok}
    return jsonify(body), 200 if ok else 503


@app.route("/api/latest")
@require_auth
def latest():
    """Latest temp/humidity from the main node + combined radar occupancy (widget)."""
    device = "home_ping_esp32"

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, temp, humidity, uptime
        FROM readings
        WHERE device = ?
        ORDER BY id DESC LIMIT 1
    """, (device,))
    row = c.fetchone() or (None, None, None, None)
    c.execute("SELECT last_seen FROM devices WHERE device = ?", (device,))
    dev = c.fetchone()
    conn.close()

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
    conn = sqlite3.connect(DB)
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
    c.execute("SELECT DISTINCT device FROM readings")
    for device in [row[0] for row in c.fetchall()]:
        c.execute("""
            SELECT ts, temp, humidity, uptime, co2, pressure, co2_ok
            FROM readings
            WHERE device = ?
            ORDER BY id DESC LIMIT 1
        """, (device,))
        row = c.fetchone()
        if not row:
            continue

        result[device] = {
            "ts": iso(row[0]),
            "temp": row[1],
            "humidity": row[2],
            "uptime": row[3],
            "co2": row[4],
            "pressure": row[5],
            "online": is_online(device),
            "co2_ok": None if row[6] is None else bool(row[6]),
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

    conn.close()

    result["combined"] = {
        "occupied": get_combined_occupancy(),
        "confidence": calculate_combined_confidence()
    }

    return jsonify(result)
@app.route("/api/restarts")
@require_auth
def restarts():
    """Restart counts per device, last 24h and 7d, plus the most recent."""
    conn = sqlite3.connect(DB)
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
    conn.close()

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
    device = request.args.get("device", "home_ping_esp32")
    # days=1: today; days=N: today and the N-1 days before it (history screen, #82).
    # Readings are kept for 8 days, so 7 full days is the most there is.
    days = request.args.get("days", "1")
    if not days.isdigit() or not 1 <= int(days) <= HOURLY_MAX_DAYS:
        return jsonify({"error": f"days must be 1-{HOURLY_MAX_DAYS}"}), 400
    days = int(days)
    since = f"-{days - 1} days"

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT
            strftime('%Y-%m-%d %H:00', ts, 'localtime'),
            AVG(temp),
            AVG(humidity)
        FROM readings
        WHERE device = ? AND ts >= datetime('now','localtime','start of day',?,'utc')
        GROUP BY 1
    """, (device, since))
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
    conn.close()
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
    conn = sqlite3.connect(DB)
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
    conn.close()

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
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT device, last_seen FROM devices")
    rows = c.fetchall()
    conn.close()

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
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT temp, humidity
        FROM readings
        WHERE ts >= datetime('now','-24 hours')
          AND temp IS NOT NULL
          AND humidity IS NOT NULL
    """)
    rows = c.fetchall()
    conn.close()

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
# -------------------------
# RUN
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
