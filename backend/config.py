"""Settings: .env values and every constant the backend uses (#73)."""

import os
import re
from dotenv import load_dotenv

load_dotenv()

# Logging (#66): DEBUG also logs every MQTT message (topic, payload, presence line);
# INFO logs alerts, connects and state changes; WARNING/ERROR only problems.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# -------------------------
# AUTH (HTTP Basic for the REST API)
# -------------------------
AUTH_USER = os.environ["DASH_USER"]
AUTH_PASS = os.environ["DASH_PASS"]

# -------------------------
# CONFIG
# -------------------------
# Files and broker
DB = "data.db"
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")   # API listen address (#59); nginx proxies to it
PORT = int(os.environ.get("PORT", "5000"))
FIREBASE_CRED = "firebase_service_account.json"
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASS = os.environ["MQTT_PASS"]
TOPIC = "home/esp32/#"  # Subscribe to all topics under home/esp32/
# Hourly occupancy for the web page (outside home/esp32/# so we don't receive our own messages)
OCCUPANCY_HOURLY_TOPIC = "home/backend/occupancy_hourly"
OCCUPANCY_HOURLY_INTERVAL = 300  # seconds; also the interval of HISTORY_24H_TOPIC
HISTORY_24H_TOPIC = "home/backend/history_24h"   # web sparklines on page load (#132)

# Retention
EVENTS_RETENTION_DAYS = 30    # events older than this are deleted
DEVICES_RETENTION_DAYS = 30   # devices not seen for this long are removed from the list

# Devices
HEARTBEAT_TIMEOUT = 120  # seconds without a message before a device counts as offline
NODE_OFFLINE_ALERT_SECONDS = 300  # no data this long -> "node offline" push (longer than
                                  # HEARTBEAT_TIMEOUT so a reboot or WiFi blip doesn't alert)
NODE_CHECK_INTERVAL = 30          # seconds between offline checks
UPTIME_DROP_TOLERANCE = 120   # seconds; a restart needs uptime to drop by more (see note_uptime)
HEALTH_MAX_MESSAGE_AGE = 300   # /api/health fails when no live MQTT message came for this long (#77)

# Presence (LD2410C radar) — the ONLY source of occupancy data.
# PIR "occupied" fields from other devices are ignored.
PRESENCE_DEVICE = "home_ping_esp32"
PRESENCE_SIGNAL_THRESHOLD = 15   # stationary readings below this energy are treated as noise
# "confidence" everywhere in the API: % of a radar's rows in the last
# CONFIDENCE_MINUTES that were occupied (0-100), or None without radar data.
CONFIDENCE_MINUTES = 15
HOURLY_MAX_DAYS = 7            # /api/hourly?days= limit (readings are kept 8 days)
DAILY_MAX_DAYS = 400           # /api/daily?days= limit (the hourly table is kept for good, #129)

# Alerts
TEMP_THRESHOLD = 18
HUMIDITY_HIGH_THRESHOLD = 65
HUMIDITY_LOW_THRESHOLD = 25
# Once per excursion (#117): an alerted humidity/temperature condition only ends once the
# value is back by this margin (high humidity < 63 %, low > 27 %, temperature > 18.5 °C)
HUMIDITY_HYSTERESIS = 2
# BME280 operating range: a reading outside it (e.g. 178 °C / 100 % / -121 hPa from a
# disconnected sensor) is dropped as a whole (#57)
ENV_VALID = {"temp_c": (-40, 85), "humidity": (0, 100), "pressure": (300, 1100)}
TEMP_HYSTERESIS = 0.5
# Mould risk hint (#177): each of the last MOULD_HOURS complete hours averaged above this
MOULD_HUMIDITY = 60   # %RH, the top of the "Comfortable" band
MOULD_HOURS = 3
NOTIFICATION_COOLDOWN = 15 * 60  # 15 minutes per alert key
PUSH_RETRY_SECONDS = 60          # retry a failed push after this, not after the full cooldown
QUIET_MINUTES_BEFORE_ALERT = 30   # flat must be quiet this long before an arrival alerts (60 until 2026-10-17)
ARRIVAL_CONFIRM_SECONDS = 120     # movement this soon after the quiet spell ends confirms the arrival
# Server watchdog pushes (#176), checked every OCCUPANCY_HOURLY_INTERVAL
BACKUP_DIR = "backups"           # backup_db.py's folder; no folder = no backup check (dev)
BACKUP_MAX_AGE_HOURS = 26        # nightly at 03:30, plus slack for a slow or late run
DISK_MIN_FREE_MB = 1024          # push below this much free space on the database's disk
DISK_FREE_HYSTERESIS_MB = 256    # ...and "OK again" only above min + this
# Notification settings (#82)
NOTIFY_KINDS = ("humidity", "temperature", "radar", "nodes")
QUIET_DEFAULTS = {"start": "22:00", "end": "07:00"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
