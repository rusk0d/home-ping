"""MQTT ingestion: message callbacks, presence + arrival alert, hourly occupancy publish.
"""

import json
import logging
import time
from datetime import datetime

import state
from config import (
    ARRIVAL_CONFIRM_SECONDS, ENV_VALID, HUMIDITY_HIGH_THRESHOLD, HUMIDITY_HYSTERESIS, HUMIDITY_LOW_THRESHOLD,
    HISTORY_24H_TOPIC, NOTIFICATION_COOLDOWN, OCCUPANCY_HOURLY_INTERVAL, OCCUPANCY_HOURLY_TOPIC,
    PRESENCE_DEVICE, PRESENCE_SIGNAL_THRESHOLD, QUIET_MINUTES_BEFORE_ALERT, TEMP_HYSTERESIS, TEMP_THRESHOLD,
    TOPIC,
)
from store import (
    db, get_last_occupancy, get_state, log_event, log_presence, log_to_db, note_uptime,
    parse_ts, rollup_hourly, set_state, utcnow,
)
from alerts import alert, set_radar_alerted
from nodes import handle_status, touch_device
from server_checks import check_watchdogs

log = logging.getLogger(__name__)


def any_radar_quiet_minutes():
    """Minutes since ANY radar last reported occupancy (call before logging the current row)."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT ts FROM presence_readings
            WHERE occupied = 1
            ORDER BY id DESC LIMIT 1
        """)
        row = c.fetchone()

    if not row:
        return 9999
    last_occ = parse_ts(row[0])
    return (utcnow() - last_occ).total_seconds() / 60


def read_int(data, key):
    """data[key] as an int, or None if missing or not a number. For uptime (#226) a default
    of 0 would look like a reboot to note_uptime() and log a bogus RESTART."""
    try:
        return int(data[key])
    except (KeyError, TypeError, ValueError):
        return None


def handle_presence_data(data):
    """Handle LD2410C radar presence data — the only occupancy source."""
    device = data.get("device", PRESENCE_DEVICE)
    presence = 1 if data.get("presence", False) else 0
    stationary = 1 if data.get("stationary", False) else 0
    moving = 1 if data.get("moving", False) else 0
    distance = int(data.get("distance", 0) or 0)
    signal = int(data.get("signal", 0) or 0)
    rssi = int(data.get("rssi", 0) or 0)
    uptime = read_int(data, "uptime")
    radar_connected = 1 if data.get("radar_connected", True) else 0

    # Filter noise: moving always counts, stationary only with a decent signal
    occupied = 1 if (moving or (stationary and signal >= PRESENCE_SIGNAL_THRESHOLD)) else 0

    # How long has the whole flat been quiet? Read BEFORE logging this row.
    quiet_minutes = any_radar_quiet_minutes()

    log_presence(device, occupied, presence, stationary, moving,
                 distance, signal, rssi, uptime, radar_connected)
    touch_device(device)
    note_uptime(device, uptime, read_int(data, "reset_reason"))

    prev_occ = get_last_occupancy(device)
    if prev_occ is not None and prev_occ != occupied:
        log_event(device, "OCCUPANCY_CHANGE", "occupied" if occupied else "vacant")

    now = time.time()

    # -------------------------
    # RADAR OFFLINE — only after it has been gone for a while,
    # so a single eink-refresh blip does not notify
    # -------------------------
    if not radar_connected:
        state.RADAR_FAIL_COUNT[device] = state.RADAR_FAIL_COUNT.get(device, 0) + 1
    else:
        state.RADAR_FAIL_COUNT[device] = 0
        if device in state.RADAR_ALERTED:  # back: the next outage alerts again
            set_radar_alerted(device, False)

    # Once per outage (not every cooldown while it stays down)
    last_radar_alert = state.LAST_NOTIFICATION.get(f"RADAR_{device}", 0)
    if (state.RADAR_FAIL_COUNT.get(device, 0) >= 12
            and device not in state.RADAR_ALERTED
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

    log.debug("presence %s: occupied=%s moving=%s stationary=%s dist=%scm signal=%s "
              "rssi=%s quiet=%dmin", device, occupied, moving, stationary, distance, signal,
              rssi, quiet_minutes)


# An alerted humidity/temperature condition (#117), in system_state so it survives a restart:
# env_alerted:<HUMIDITY|TEMP>_<device> = "high" / "low" while that excursion has been pushed
# (or muted); removed once the value is back in range by the hysteresis margin.
def env_alerted(key):
    return get_state(f"env_alerted:{key}", "") or None


def set_env_alerted(key, band):
    set_state(f"env_alerted:{key}", band or "")


def on_message(client, userdata, msg):
    # Nothing may escape this callback: paho re-raises callback exceptions, which
    # ends its network thread and silently stops all ingestion (#58). So the whole
    # body is in the try, and the payload is decoded leniently (bad bytes -> U+FFFD).
    try:
        payload = msg.payload.decode("utf-8", errors="replace")
        log.debug("received on %s: %s", msg.topic, payload)
        # The broker replays the last retained message of each topic when we
        # (re)subscribe, with the retain flag set. That's an old reading: storing it
        # would add a duplicate row with a fresh ts and could re-send alerts (#5).
        # Live messages arrive without the flag. The firmware keeps retaining for
        # the web page, which wants the last value immediately.
        if msg.retain:
            log.debug("skipping retained message on %s", msg.topic)
            return
        state.LAST_MESSAGE_AT = time.time()

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
        # Every sketch sends its device ID; without one there's no device to store it
        # under (the old default "esp32" made a phantom device and an offline push, #170)
        device = data.get("device")
        if not isinstance(device, str) or not device:
            log.warning("payload on %s without a device, dropped: %.200s", msg.topic, payload)
            return
        temp = data.get("temp_c")
        hum = data.get("humidity")
        uptime = read_int(data, "uptime")
        co2 = data.get("co2")
        pressure = data.get("pressure")

        # A broken or disconnected sensor reports values outside its range (e.g. 178 °C,
        # 100 %, -121 hPa): drop temperature, humidity and pressure together, since they
        # come from the same chip (#57)
        bad = {k: data[k] for k, (lo, hi) in ENV_VALID.items()
               if isinstance(data.get(k), (int, float)) and not lo <= data[k] <= hi}
        if bad:
            log.warning("%s: implausible env reading %s, dropped", device, bad)
            temp = hum = pressure = None

        # co2_ok is stored with the reading so it survives a backend restart
        co2_ok = (1 if data["co2_ok"] else 0) if "co2_ok" in data else None

        log_to_db(device, temp, hum, uptime, co2, pressure, co2_ok)
        touch_device(device)
        note_uptime(device, uptime, read_int(data, "reset_reason"))

        now = time.time()

        # Each alert only needs its own value (a node may send just one of them)

        # -------------------------
        # HUMIDITY ALERT (once per excursion, #117)
        # -------------------------
        if hum is not None:
            if hum > HUMIDITY_HIGH_THRESHOLD:
                band = "high"
            elif hum < HUMIDITY_LOW_THRESHOLD:
                band = "low"
            else:
                band = None
            key = f"HUMIDITY_{device}"
            alerted = env_alerted(key)
            # Back in range by the hysteresis margin: the excursion is over
            if (alerted == "high" and hum < HUMIDITY_HIGH_THRESHOLD - HUMIDITY_HYSTERESIS
                    or alerted == "low" and hum > HUMIDITY_LOW_THRESHOLD + HUMIDITY_HYSTERESIS):
                set_env_alerted(key, None)
                alerted = None
            if band and band != alerted and now - state.LAST_NOTIFICATION.get(key, 0) > NOTIFICATION_COOLDOWN:
                if alert(device, f"HUMIDITY_{band.upper()}_ALERT", f"humidity={hum}",
                         title="Humidity Alert",
                         body=f"Humidity is {band}: {hum:.1f}%",
                         data={"event": f"HUMIDITY_{band.upper()}", "device": device},
                         cooldown_key=key, kind="humidity"):
                    set_env_alerted(key, band)

        # -------------------------
        # TEMPERATURE ALERT (once per excursion, #117)
        # -------------------------
        if temp is not None:
            key = f"TEMP_{device}"
            alerted = env_alerted(key)
            if alerted and temp > TEMP_THRESHOLD + TEMP_HYSTERESIS:
                set_env_alerted(key, None)
                alerted = None
            if (temp < TEMP_THRESHOLD and not alerted
                    and now - state.LAST_NOTIFICATION.get(key, 0) > NOTIFICATION_COOLDOWN):
                if alert(device, "TEMP_ALERT", f"temp={temp}",
                         title="Temperature Alert",
                         body=f"Temperature is low on {device}: {temp:.1f}°C",
                         data={"event": "TEMP", "device": device},
                         cooldown_key=key, kind="temperature"):
                    set_env_alerted(key, "low")

    except Exception as e:
        log.error("MQTT error on %s: %r", getattr(msg, "topic", "?"), e)


# -------------------------
# MQTT SETUP
# -------------------------
def hourly_occupancy_today(days=1):
    """{'YYYY-MM-DD HH:00': mean(occupied)*100} for PRESENCE_DEVICE since local midnight
    (days - 1) days ago; days=1 is today."""
    with db() as conn:
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
    state.mqttc.publish(OCCUPANCY_HOURLY_TOPIC, json.dumps(payload), qos=0, retain=True)


def history_24h():
    """Hourly averages of the last 24 complete hours, oldest first (#132). Only hours
    with data; a value is null when no device reported it that hour."""
    with db() as conn:
        rows = conn.execute("""
            SELECT strftime('%Y-%m-%dT%H:00:00Z', ts), AVG(temp), AVG(humidity), AVG(pressure), AVG(co2)
            FROM readings
            WHERE ts >= datetime(strftime('%Y-%m-%d %H:00:00', 'now'), '-24 hours')
              AND ts < strftime('%Y-%m-%d %H:00:00', 'now')
            GROUP BY 1 ORDER BY 1
        """).fetchall()

    def r(v, digits):
        return None if v is None else round(v, digits)
    return {
        "updated": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hours": [{"start": h, "temp": r(t, 1), "humidity": r(hu, 1), "pressure": r(p, 1),
                   "co2": None if c is None else round(c)} for h, t, hu, p, c in rows],
    }


def publish_history_24h():
    state.mqttc.publish(HISTORY_24H_TOPIC, json.dumps(history_24h()), qos=0, retain=True)


def publish_summaries():
    """The backend's retained topics, each on its own so one failing doesn't stop the other."""
    for name, publish in (("hourly occupancy", publish_hourly_occupancy), ("24 h history", publish_history_24h)):
        try:
            publish()
        except Exception as e:
            log.warning("%s publish failed: %s", name, e)


def summaries_loop():
    while True:
        time.sleep(OCCUPANCY_HOURLY_INTERVAL)
        try:
            rollup_hourly()   # only does work once another hour has completed (#129)
        except Exception as e:
            log.warning("hourly rollup failed: %s", e)
        publish_summaries()
        try:
            check_watchdogs()   # backup overdue, disk almost full (#176), certificates (#206)
        except Exception as e:
            log.warning("watchdog check failed: %s", e)


# paho callback API v2 (#66). A refused connect also arrives here, as a failure code;
# paho keeps retrying in its loop either way.
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        log.warning("MQTT connect refused: %s", reason_code)
        return
    log.info("MQTT connected")
    client.subscribe(TOPIC)
    publish_summaries()


def on_disconnect(client, userdata, flags, reason_code, properties):
    log.warning("MQTT disconnected: %s (reconnecting)", reason_code)
