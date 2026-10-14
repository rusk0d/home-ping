import json
import sqlite3
import os
import re
from flask import Flask, jsonify, request, Response
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
from functools import wraps
from dotenv import load_dotenv
import time
import threading
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import requests


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
MAX_EVENTS = 3 
HEARTBEAT_TIMEOUT = 300  # seconds
FIREBASE_CRED = "firebase_service_account.json"
SOIL_DRY_THRESHOLD = 25
LAST_UPTIME = {}
UPTIME_DROP_TOLERANCE = 120   # seconds; see note below
# Presence (LD2410C radar) — the ONLY source of occupancy data.
# PIR "occupied" fields from other devices are ignored.
PRESENCE_DEVICE = "home_ping_esp32"
PRESENCE_SIGNAL_THRESHOLD = 15   # stationary readings below this energy are treated as noise
# Hourly occupancy for the web page (outside home/esp32/# so we don't receive our own messages)
OCCUPANCY_HOURLY_TOPIC = "home/backend/occupancy_hourly"
OCCUPANCY_HOURLY_INTERVAL = 300  # seconds

app = Flask(__name__)

# -------------------------
# FIREBASE INIT
# -------------------------
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CRED)
    firebase_admin.initialize_app(cred)

# -------------------------
# DATABASE INIT
# -------------------------
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Modified readings table to include device column
    c.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            ts TIMESTAMP,
            temp REAL,
            humidity REAL,
            uptime INTEGER,
            occupied INTEGER,
            occupancy_confidence INTEGER,
            dark INTEGER,
            co2 INTEGER,
            pressure REAL
        )
    """)

    # Add columns missing from older databases (including ones created by the
    # removed init_db.py, which had only id/ts/temp/humidity)
    for col, coltype in (("device", "TEXT"), ("uptime", "INTEGER"),
                         ("occupied", "INTEGER"), ("occupancy_confidence", "INTEGER"),
                         ("dark", "INTEGER"), ("co2", "INTEGER"), ("pressure", "REAL")):
        try:
            c.execute(f"ALTER TABLE readings ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device TEXT PRIMARY KEY,
            last_seen TIMESTAMP
        )
    """)

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
    c.execute("""
        CREATE TABLE IF NOT EXISTS soil_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            ts TIMESTAMP,
            moisture INTEGER,
            raw_value INTEGER
        )
    """)

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
        VALUES (?, datetime('now','localtime'))
        ON CONFLICT(device)
        DO UPDATE SET last_seen=datetime('now','localtime')
    """, (device,))
    conn.commit()
    conn.close()

def log_to_db(device, temp, hum, uptime, occupied, conf, dark, co2=None, pressure=None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
        INSERT INTO readings
        (device, ts, temp, humidity, uptime, occupied, occupancy_confidence,
         dark, co2, pressure)
        VALUES (?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?)
    """, (device, temp, hum, uptime, occupied, conf, dark, co2, pressure))

    c.execute("""
        DELETE FROM readings
        WHERE ts < datetime('now','localtime','-8 days')
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
        VALUES (?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (device, occupied, presence, stationary, moving,
          distance, signal, rssi, uptime, radar_connected))

    # Keep 8 days so the 24h queries always have full coverage
    c.execute("""
        DELETE FROM presence_readings
        WHERE ts < datetime('now','localtime','-8 days')
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
        WHERE ts >= datetime('now','localtime', ?)
        GROUP BY device
    """, (f"-{hours} hours",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"avg_occupancy": 0, "occupied_hours": 0, "coverage_hours": 0}

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
    
def calculate_confidence(device, minutes):
    """Occupancy confidence from the radar over a time window (0-100)."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT occupied FROM presence_readings
        WHERE device = ? AND ts >= datetime('now','localtime', ?)
    """, (device, f"-{minutes} minutes"))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return 0

    return int((sum(r[0] for r in rows) / len(rows)) * 100)

def get_combined_occupancy():
    """Occupied if any radar has 2+ occupied rows in its last 3 reports."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT DISTINCT device FROM presence_readings")
    devices = [r[0] for r in c.fetchall()]

    result = 0
    for d in devices:
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ? AND ts >= datetime('now','localtime','-2 minutes')
            ORDER BY id DESC LIMIT 3
        """, (d,))
        rows = [r[0] for r in c.fetchall()]
        if sum(rows) >= 2:
            result = 1
    conn.close()
    return result


def calculate_combined_confidence(minutes):
    """Per-radar average over the window, then the highest of them."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT device, AVG(occupied)
        FROM presence_readings
        WHERE ts >= datetime('now','localtime', ?)
        GROUP BY device
    """, (f"-{minutes} minutes",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return 0
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

def get_latest_presence():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, occupied, presence, stationary, moving,
               distance, signal, rssi, uptime, radar_connected
        FROM presence_readings
        WHERE device = ?
        ORDER BY id DESC LIMIT 1
    """, (PRESENCE_DEVICE,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "ts": row[0],
        "occupied": row[1],
        "presence": row[2],
        "stationary": row[3],
        "moving": row[4],
        "distance": row[5],
        "signal": row[6],
        "rssi": row[7],
        "uptime": row[8],
        "radar_connected": row[9],
    }

def log_event(device, event_type, value):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO events (device, ts, type, value)
        VALUES (?, datetime('now','localtime'), ?, ?)
    """, (device, event_type, value))
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

    # A real reboot resets uptime to near zero. Require a meaningful drop,
    # not just a smaller number (see note on multi-topic devices).
    if uptime < prev - UPTIME_DROP_TOLERANCE:
        log_event(device, "RESTART", f"uptime {prev}s -> {uptime}s")
        print(f"[RESTART] {device}: was up {prev}s, now {uptime}s")
# -------------------------
# PUSH NOTIFICATIONS (FCM)
# -------------------------
def send_push(title, body, data=None):
    print(f"[DEBUG] Preparing to send push: title={title}, body={body}, data={data}")
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data=data or {},
            topic="env"
        )
        response = messaging.send(message)
        print(f"[DEBUG] Push sent! Response: {response}")
    except Exception as e:
        print(f"[ERROR] Failed to send push: {e}")
def get_access_token():
    """Refresh and return the Google OAuth access token."""
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds.token




def handle_soil_data(data, topic):
    """Handle soil moisture sensor data"""
    device = data.get("device", "soil_sensor_01")
    moisture = data.get("moisture")
    raw = data.get("raw")
    
    # Log to database
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO soil_readings (device, ts, moisture, raw_value)
        VALUES (?, datetime('now','localtime'), ?, ?)
    """, (device, moisture, raw))
    
    # Keep last 1000 readings
    c.execute("""
        DELETE FROM soil_readings
        WHERE device = ? AND id NOT IN (
            SELECT id FROM soil_readings
            WHERE device = ?
            ORDER BY id DESC
            LIMIT 1000
        )
    """, (device, device))
    
    # Update device heartbeat
    c.execute("""
        INSERT INTO devices (device, last_seen)
        VALUES (?, datetime('now','localtime'))
        ON CONFLICT(device)
        DO UPDATE SET last_seen=datetime('now','localtime')
    """, (device,))
    
    conn.commit()
    conn.close()
    
    # Send alert if too dry
    now = time.time()
    last_dry_alert = LAST_NOTIFICATION.get(f"SOIL_DRY_{device}", 0)
    
    if moisture < SOIL_DRY_THRESHOLD and (now - last_dry_alert > NOTIFICATION_COOLDOWN):
        send_push(
            title="Plant Needs Water",
            body=f"{device}: Soil moisture is low ({moisture}%)",
            data={"event": "SOIL_DRY", "device": device, "moisture": str(moisture)}
        )
        log_event(device, "SOIL_DRY_ALERT", f"moisture={moisture}%")
        LAST_NOTIFICATION[f"SOIL_DRY_{device}"] = now
    
    print(f"[SOIL] {device}: {moisture}% (raw: {raw})")

# ---- add near the other config constants, top of the file ----
QUIET_MINUTES_BEFORE_ALERT = 60   # flat must be quiet this long before an arrival alerts


def any_radar_quiet_minutes():
    """Minutes since ANY radar last reported occupancy, excluding the newest row."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts FROM presence_readings
        WHERE occupied = 1 AND id < (SELECT MAX(id) FROM presence_readings)
        ORDER BY id DESC LIMIT 1
    """)
    row = c.fetchone()
    conn.close()

    if not row:
        return 9999
    last_occ = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - last_occ).total_seconds() / 60


def handle_presence_data(data, topic):
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

    last_radar_alert = LAST_NOTIFICATION.get(f"RADAR_{device}", 0)
    if (RADAR_FAIL_COUNT.get(device, 0) >= 12
            and now - last_radar_alert > NOTIFICATION_COOLDOWN):
        send_push(
            title="Radar Disconnected",
            body=f"{device}: LD2410C not responding",
            data={"event": "RADAR_DISCONNECTED", "device": device}
        )
        log_event(device, "RADAR_DISCONNECTED", "no frames from LD2410C")
        LAST_NOTIFICATION[f"RADAR_{device}"] = now

    # -------------------------
    # OCCUPANCY ALERT — fires once per arrival, after a long quiet spell
    # -------------------------
    if (get_state("occupancy_armed") == "1"
            and occupied
            and moving
            and quiet_minutes >= QUIET_MINUTES_BEFORE_ALERT):
        where = "hallway" if "hallway" in device else "main room"
        send_push(
            title="Occupancy Alert",
            body=f"Movement in the {where} after {int(quiet_minutes)} min of quiet ({distance}cm)",
            data={"event": "OCCUPIED", "device": device, "distance": str(distance)}
        )
        log_event(device, "OCCUPANCY_ALERT", f"after {int(quiet_minutes)} min quiet")

    print(f"[PRESENCE] {device}: occupied={occupied} moving={moving} "
          f"stationary={stationary} dist={distance}cm signal={signal} "
          f"rssi={rssi} quiet={int(quiet_minutes)}min")

# -------------------------
# MQTT CALLBACK
# -------------------------
LAST_NOTIFICATION = {}
RADAR_FAIL_COUNT = {}
CO2_SENSOR_OK = {}
NOTIFICATION_COOLDOWN = 10 * 90  # 15 minutes
TEMP_THRESHOLD = 18
HUMIDITY_HIGH_THRESHOLD = 65
HUMIDITY_LOW_THRESHOLD = 25
HEARTBEAT_TIMEOUT = 120  # seconds before device is considered offline
CONFIDENCE_THRESHOLD = 65

def on_message(client, userdata, msg):
    print(f"[MQTT] Received message on topic: {msg.topic}")
    print(f"[MQTT] Payload: {msg.payload.decode()}")
    try:
        data = json.loads(msg.payload.decode())

        if "moisture" in data and "raw" in data:
            handle_soil_data(data, msg.topic)
            return

        if "presence" in data:
            handle_presence_data(data, msg.topic)
            return

        # ---- Everything else: environmental sensors. PIR fields are ignored. ----
        device = data.get("device", "esp32")
        temp = data.get("temp_c")
        hum = data.get("humidity")
        uptime = data.get("uptime", 0)
        dark = 1 if data.get("dark", 0) else 0
        co2 = data.get("co2")
        pressure = data.get("pressure")

        # occupied / confidence intentionally 0 for non-radar devices
        log_to_db(device, temp, hum, uptime, 0, 0, dark, co2, pressure)
        touch_device(device)
        note_uptime(device, uptime)        
        if "co2_ok" in data:
            CO2_SENSOR_OK[device] = bool(data["co2_ok"])

        now = time.time()

        # Only send temp/humidity alerts for devices with those sensors
        if temp is None or hum is None:
            return
        
        # -------------------------
        # HUMIDITY ALERT
        # -------------------------
        last_hum_sent = LAST_NOTIFICATION.get(f"HUMIDITY_{device}", 0)
        if now - last_hum_sent > NOTIFICATION_COOLDOWN:
            if hum > HUMIDITY_HIGH_THRESHOLD:
                send_push(
                    title="Humidity Alert",
                    body=f"Humidity is high: {hum:.1f}%",
                    data={"event": "HUMIDITY_HIGH", "device": device}
                )
                log_event(device, "HUMIDITY_HIGH_ALERT", f"sent, humidity={hum}")
                LAST_NOTIFICATION[f"HUMIDITY_{device}"] = now

            elif hum < HUMIDITY_LOW_THRESHOLD:
                send_push(
                    title="Humidity Alert",
                    body=f"Humidity is low: {hum:.1f}%",
                    data={"event": "HUMIDITY_LOW", "device": device}
                )
                log_event(device, "HUMIDITY_LOW_ALERT", f"sent, humidity={hum}")
                LAST_NOTIFICATION[f"HUMIDITY_{device}"] = now   

        # -------------------------
        # TEMPERATURE ALERT
        # -------------------------
        last_temp_sent = LAST_NOTIFICATION.get(f"TEMP_{device}", 0)
        if temp < TEMP_THRESHOLD and (now - last_temp_sent > NOTIFICATION_COOLDOWN):
            send_push(
                title="Temperature Alert",
                body=f"Temperature is low on {device}: {temp:.1f}°C",
                data={"event": "TEMP", "device": device}
            )
            log_event(device, "TEMP_ALERT", f"sent, temp={temp}")
            LAST_NOTIFICATION[f"TEMP_{device}"] = now

    except Exception as e:
        print("MQTT error:", e)

# -------------------------
# MQTT SETUP
# -------------------------
def hourly_occupancy_today():
    """{'YYYY-MM-DD HH:00': mean(occupied)*100} for PRESENCE_DEVICE since local midnight."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT
            strftime('%Y-%m-%d %H:00', ts),
            AVG(occupied)*100
        FROM presence_readings
        WHERE device = ? AND ts >= datetime('now','localtime','start of day')
        GROUP BY 1
    """, (PRESENCE_DEVICE,))
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
# -------------------------
# API ROUTES
# -------------------------
@app.route('/api/next-events')
def next_events():
    try:
        calendar_ids = list_calendars()  # primary + ICS + any other
        all_events = []
        for cid in calendar_ids:
            all_events += get_events(cid)

        # sort all events by start time
        all_events.sort(key=lambda e: e['start'])
        next_three = all_events[:MAX_EVENTS]

        return Response(
            json.dumps(next_three, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
        )

    except Exception as e:
        return Response(
            json.dumps({'error': 'internal_exception', 'message': str(e)}, ensure_ascii=False),
            status=500,
            content_type='application/json; charset=utf-8'
        )


@app.route("/api/latest")
@require_auth
def latest():
    """Latest temp/humidity from the OLED node + occupancy from the radar node"""
    device = "home_ping_esp32"
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
        SELECT ts, temp, humidity, uptime, occupied, occupancy_confidence, dark
        FROM readings 
        WHERE device = ?
        ORDER BY id DESC LIMIT 1
    """, (device,))
    row = c.fetchone()

    c.execute("SELECT last_seen FROM devices WHERE device = ?", (device,))
    dev = c.fetchone()
    conn.close()

    if not row:
        return jsonify([])

    online = False
    if dev:
        last_seen = datetime.strptime(dev[0], "%Y-%m-%d %H:%M:%S")
        online = (datetime.now() - last_seen).total_seconds() < HEARTBEAT_TIMEOUT

    return jsonify([
        row[0],                            # timestamp
        row[1],                            # temp (from OLED ESP32)
        row[2],                            # humidity (from OLED ESP32)
        row[3],                            # uptime
        get_combined_occupancy(),          # radar occupancy
        calculate_combined_confidence(15), # radar confidence
        row[6],                            # dark
        online
    ])

@app.route("/api/latest_all")
@require_auth
def latest_all():
    """Latest readings from every device — env nodes and radar nodes."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    now = datetime.now()

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
            SELECT ts, temp, humidity, uptime, dark, co2, pressure
            FROM readings
            WHERE device = ?
            ORDER BY id DESC LIMIT 1
        """, (device,))
        row = c.fetchone()
        if not row:
            continue

        result[device] = {
            "ts": row[0],
            "temp": row[1],
            "humidity": row[2],
            "uptime": row[3],
            "occupied": 0,
            "occupancy_confidence": 0,
            "dark": row[4],
            "co2": row[5],
            "pressure": row[6],
            "online": is_online(device),
            "co2_ok": CO2_SENSOR_OK.get(device),            
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
            "dark": None,
            "co2": None,
            "pressure": None,
        })

        entry.update({
            "ts": p[0],
            "uptime": p[8],
            "occupied": p[1],
            "occupancy_confidence": calculate_confidence(device, 15),
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
        "confidence": calculate_combined_confidence(15)
    }

    return jsonify(result)
@app.route("/api/presence/latest")
@require_auth
def presence_latest():
    """Latest radar reading"""
    presence = get_latest_presence()
    if not presence:
        return jsonify({"error": "No data"}), 404

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT last_seen FROM devices WHERE device = ?", (PRESENCE_DEVICE,))
    dev = c.fetchone()
    conn.close()

    online = False
    if dev:
        last_seen = datetime.strptime(dev[0], "%Y-%m-%d %H:%M:%S")
        online = (datetime.now() - last_seen).total_seconds() < HEARTBEAT_TIMEOUT

    presence["online"] = online
    presence["occupancy_confidence"] = calculate_confidence(PRESENCE_DEVICE, 15)
    presence["occupancy_armed"] = get_state("occupancy_armed") == "1"
    return jsonify(presence)

@app.route("/api/presence/history")
@require_auth
def presence_history():
    """Radar reading history"""
    limit = int(request.args.get("limit", 100))
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, occupied, presence, stationary, moving, distance, signal, rssi
        FROM presence_readings
        WHERE device = ?
        ORDER BY id DESC LIMIT ?
    """, (PRESENCE_DEVICE, limit))
    rows = c.fetchall()
    conn.close()
    return jsonify([{
        "ts": r[0], "occupied": r[1], "presence": r[2], "stationary": r[3],
        "moving": r[4], "distance": r[5], "signal": r[6], "rssi": r[7]
    } for r in rows])

@app.route("/api/latest2")
@require_auth
def latest2():
    device = request.args.get("device", "home_ping_esp32")
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
        SELECT ts, temp, humidity, occupied, occupancy_confidence, dark
        FROM readings
        WHERE device = ?
        ORDER BY id DESC
        LIMIT 2
    """, (device,))
    rows = c.fetchall()
    conn.close()

    return jsonify(rows)

@app.route("/api/history")
@require_auth
def history():
    device = request.args.get("device", "home_ping_esp32")
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, temp, humidity, uptime, occupied, occupancy_confidence, dark
        FROM readings 
        WHERE device = ?
        ORDER BY id DESC LIMIT 30
    """, (device,))
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

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
          AND ts >= datetime('now','localtime','-24 hours')
        GROUP BY device
    """)
    day = dict(c.fetchall())

    c.execute("""
        SELECT device, COUNT(*)
        FROM events
        WHERE type = 'RESTART'
          AND ts >= datetime('now','localtime','-7 days')
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
            "last_restart": last.get(d),
        } for d in devices
    })

@app.route("/api/stats24h")
@require_auth
def stats24h():
    device = request.args.get("device", "home_ping_esp32")
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT temp, humidity
        FROM readings
        WHERE device = ? AND ts >= datetime('now','localtime','-24 hours')
          AND temp IS NOT NULL
          AND humidity IS NOT NULL
    """, (device,))
    rows = c.fetchall()
    conn.close()

    occ = occupancy_stats(24)

    if not rows:
        return jsonify({
            "avg_temp": 0,
            "avg_humidity": 0,
            "avg_occupancy": occ["avg_occupancy"],
            "avg_confidence": calculate_combined_confidence(60),
            "occupied_hours": occ["occupied_hours"],
            "coverage_hours": occ["coverage_hours"]
        })

    temps = [r[0] for r in rows]
    hums = [r[1] for r in rows]

    return jsonify({
        "avg_temp": round(sum(temps)/len(temps), 1),
        "avg_humidity": round(sum(hums)/len(hums), 1),
        "avg_occupancy": occ["avg_occupancy"],
        "avg_confidence": calculate_combined_confidence(60),
        "occupied_hours": occ["occupied_hours"],
        "coverage_hours": occ["coverage_hours"]
    })
@app.route("/api/occupancy_rolling")
@require_auth
def occupancy_rolling():
    """Occupancy confidence from the radar over a rolling window"""
    minutes = int(request.args.get("minutes", 60))
    return jsonify({
        "minutes": minutes,
        "confidence": calculate_combined_confidence(minutes)
    })

@app.route("/api/hourly")
@require_auth
def hourly():
    device = request.args.get("device", "home_ping_esp32")
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT
            strftime('%Y-%m-%d %H:00', ts),
            AVG(temp),
            AVG(humidity)
        FROM readings
        WHERE device = ? AND ts >= datetime('now','localtime','start of day')
        GROUP BY 1
    """, (device,))
    env_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}
    conn.close()
    occ_rows = hourly_occupancy_today()

    hours = sorted(set(env_rows) | set(occ_rows), reverse=True)
    out = []
    for h in hours:
        t, hu = env_rows.get(h, (None, None))
        o = occ_rows.get(h, 0)
        out.append({
            "hour": h,
            "avg_temp": round(t, 1) if t is not None else None,
            "avg_humidity": round(hu, 1) if hu is not None else None,
            "avg_occupancy": round(o, 0),
            "avg_confidence": round(o, 0)
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
          AND ts <= datetime('now','localtime','-3 hours')
        ORDER BY id DESC LIMIT 1
    """)
    old = c.fetchone()
    conn.close()

    co2 = co2_row[1] if co2_row else None
    if co2 is None:        band = "unknown"
    elif co2 < 800:        band = "good"
    elif co2 < 1200:       band = "fair"
    elif co2 < 2000:       band = "stuffy"
    else:                  band = "bad"

    pressure = env_row[1] if env_row else None
    trend = None
    if old and pressure is not None:
        trend = round(pressure - old[0], 1)

    return jsonify({
        "ts": env_row[0] if env_row else (co2_row[0] if co2_row else None),
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

    now = datetime.now()
    out = []

    for dev, ts in rows:
        last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        out.append({
            "device": dev,
            "last_seen": ts,
            "online": (now - last).total_seconds() < HEARTBEAT_TIMEOUT
        })

    return jsonify(out)

@app.route("/api/widget")
@require_auth
def widget():
    device = request.args.get("device", "home_ping_esp32")
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
        SELECT ts, temp, humidity, dark
        FROM readings 
        WHERE device = ?
        ORDER BY id DESC LIMIT 1
    """, (device,))
    r = c.fetchone()

    c.execute("""
        SELECT ts, type, value
        FROM events 
        WHERE device IN (?, ?)
        ORDER BY id DESC LIMIT 5
    """, (device, PRESENCE_DEVICE))
    events = c.fetchall()

    armed = get_state("occupancy_armed")
    conn.close()

    # Same shape as before: [ts, temp, humidity, occupied, dark]
    reading = [r[0], r[1], r[2], get_combined_occupancy(), r[3]] if r else None

    return jsonify({
        "reading": reading,
        "events": events,
        "occupancy_armed": armed == "1"
    })

@app.route("/api/register_token", methods=["POST"])
@require_auth
def register_token():
    data = request.json
    token = data.get("token")
    device = data.get("device", "unknown")

    if not token:
        return jsonify({"error": "No token provided"}), 400

    touch_device(device)
    messaging.subscribe_to_topic([token], "env")

    return jsonify({"success": True})

@app.route("/api/occupancy_arm", methods=["POST"])
@require_auth
def occupancy_arm():
    data = request.json or {}
    armed = "1" if data.get("armed") else "0"
    set_state("occupancy_armed", armed)
    log_event("system", "OCCUPANCY_ARMED", armed)
    return jsonify({"armed": armed == "1"})

@app.route("/api/occupancy_status")
@require_auth
def occupancy_status():
    return jsonify({"armed": get_state("occupancy_armed") == "1"})



@app.route("/api/stats24h_combined")
@require_auth
def stats24h_combined():
    '''Combined 24h env stats from ALL devices + radar occupancy'''
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT temp, humidity
        FROM readings
        WHERE ts >= datetime('now','localtime','-24 hours')
          AND temp IS NOT NULL
          AND humidity IS NOT NULL
    """)
    rows = c.fetchall()
    conn.close()

    occ = occupancy_stats(24)

    if not rows:
        return jsonify({
            "avg_temp": 0,
            "avg_humidity": 0,
            "avg_occupancy": occ["avg_occupancy"],
            "avg_confidence": calculate_combined_confidence(60),
            "occupied_hours": occ["occupied_hours"],
            "coverage_hours": occ["coverage_hours"]
        })

    temps = [r[0] for r in rows]
    hums = [r[1] for r in rows]

    return jsonify({
        "avg_temp": round(sum(temps)/len(temps), 1),
        "avg_humidity": round(sum(hums)/len(hums), 1),
        "avg_occupancy": occ["avg_occupancy"],
        "avg_confidence": calculate_combined_confidence(60),
        "occupied_hours": occ["occupied_hours"],
        "coverage_hours": occ["coverage_hours"]
    })
@app.route("/api/history_combined")
@require_auth

def history_combined():
    '''Last 30 readings from ALL devices, env + radar merged'''
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, device, temp, humidity, occupied, occupancy_confidence
        FROM (
            SELECT ts, device, temp, humidity, occupied, occupancy_confidence
            FROM readings
            UNION ALL
            SELECT ts, device, NULL AS temp, NULL AS humidity, occupied,
                   CASE WHEN moving THEN 100 ELSE signal END AS occupancy_confidence
            FROM presence_readings
        )
        ORDER BY ts DESC LIMIT 30
    """)
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)
@app.route("/api/soil/latest")
@require_auth
def soil_latest():
    """Get latest soil moisture reading"""
    device = request.args.get("device", "soil_sensor_01")
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, moisture, raw_value
        FROM soil_readings
        WHERE device = ?
        ORDER BY id DESC LIMIT 1
    """, (device,))
    row = c.fetchone()
    
    c.execute("SELECT last_seen FROM devices WHERE device = ?", (device,))
    dev = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "No data"}), 404
    
    online = False
    if dev:
        last_seen = datetime.strptime(dev[0], "%Y-%m-%d %H:%M:%S")
        online = (datetime.now() - last_seen).total_seconds() < HEARTBEAT_TIMEOUT
    
    return jsonify({
        "ts": row[0],
        "moisture": row[1],
        "raw": row[2],
        "online": online
    })

@app.route("/api/soil/history")
@require_auth
def soil_history():
    """Get soil moisture history"""
    device = request.args.get("device", "soil_sensor_01")
    limit = int(request.args.get("limit", 50))
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT ts, moisture, raw_value
        FROM soil_readings
        WHERE device = ?
        ORDER BY id DESC LIMIT ?
    """, (device, limit))
    rows = c.fetchall()
    conn.close()
    
    return jsonify([{
        "ts": r[0],
        "moisture": r[1],
        "raw": r[2]
    } for r in rows])

@app.route("/api/soil/stats24h")
@require_auth
def soil_stats24h():
    """Get 24h soil statistics"""
    device = request.args.get("device", "soil_sensor_01")
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT moisture
        FROM soil_readings
        WHERE device = ? AND ts >= datetime('now','localtime','-24 hours')
    """, (device,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return jsonify({
            "avg_moisture": 0,
            "min_moisture": 0,
            "max_moisture": 0
        })
    
    moistures = [r[0] for r in rows]
    
    return jsonify({
        "avg_moisture": round(sum(moistures)/len(moistures), 1),
        "min_moisture": min(moistures),
        "max_moisture": max(moistures)
    })
# -------------------------
# RUN
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
