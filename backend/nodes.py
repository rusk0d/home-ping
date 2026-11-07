"""Device heartbeat, node offline / back online alerts (#13), silent sensors (#224).
"""

import logging
import time

import state
from config import (
    NODE_CHECK_INTERVAL, NODE_OFFLINE_ALERT_SECONDS,
    SENSOR_SILENT_SECONDS,
)
from store import db, log_event, parse_ts, utcnow
from alerts import Excursion

log = logging.getLogger(__name__)


# -------------------------
# HEARTBEAT
# -------------------------
def touch_device(device):
    """Update device heartbeat"""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO devices (device, last_seen)
            VALUES (?, datetime('now'))
            ON CONFLICT(device)
            DO UPDATE SET last_seen=datetime('now')
        """, (device,))
        # (nodes gone for DEVICES_RETENTION_DAYS are dropped by store.prune())
    if device in state.NODE_OFFLINE:
        node_back_online(device)


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
    if time.time() - state.STARTED_AT < NODE_OFFLINE_ALERT_SECONDS:
        return
    with db() as conn:
        rows = conn.execute("SELECT device, last_seen FROM devices").fetchall()
    now = utcnow()
    for device, last_seen in rows:
        age = (now - parse_ts(last_seen)).total_seconds()
        if age < NODE_OFFLINE_ALERT_SECONDS:
            continue
        with state.NODE_LOCK:
            if device in state.NODE_OFFLINE:
                continue
            result = _outage(device).start("NODE_OFFLINE", f"last seen {last_seen}", "Node offline",
                                           f"{device}: no data for {int(age // 60)} min",
                                           data={"status": "offline"})
            if result:
                state.NODE_OFFLINE[device] = result == "sent"


def node_back_online(device):
    with state.NODE_LOCK:
        if state.NODE_OFFLINE.pop(device, None) is None:
            return
        _outage(device).end("NODE_ONLINE", "data again", "Node back online",
                            f"{device} is sending data again", data={"status": "online"})


def _outage(device):
    """node_offline:<device>, an Excursion (#305); state.NODE_OFFLINE is its in-memory copy
    (device -> whether the offline push was sent), for the checks on every message."""
    return Excursion(f"node_offline:{device}", "nodes", device, {"event": "NODE_STATUS", "device": device},
                     cooldown_key=f"NODE_{device}")


# -------------------------
# SENSOR SILENT / BACK (#224)
# -------------------------
# A node can stay online (radar, status, the other sensor keep last_seen fresh) while one
# of its sensors is dead: a failed BME280 publishes nothing, a failed MH-Z19C publishes
# co2_ok false. Checked for each sensor a device has in the raw data (kept
# RAW_RETENTION_DAYS): silent when there's been no valid value for SENSOR_SILENT_SECONDS
# although the node was sending other data at the start of that time and still is, so an
# outage (NODE_STATUS) or a reboot doesn't count. Once per excursion, an Excursion in
# sensor_silent:<device>:<sensor> (#305); quiet hours postpone it, like the server watchdog.
SENSORS = {   # sensor: (name in pushes, a valid value, the sensor reported at all)
    "env": ("BME280", "temp IS NOT NULL", "temp IS NOT NULL"),
    "co2": ("CO₂ sensor", "co2 IS NOT NULL AND co2_ok = 1", "co2 IS NOT NULL OR co2_ok IS NOT NULL"),
}


def check_sensors():
    silent_since = f"-{SENSOR_SILENT_SECONDS} seconds"
    alive_from = f"-{SENSOR_SILENT_SECONDS + NODE_OFFLINE_ALERT_SECONDS} seconds"
    has = ", ".join(f"MAX({reported})" for _, _, reported in SENSORS.values())
    fresh = ", ".join(f"MAX({valid} AND ts >= datetime('now', ?))" for _, valid, _ in SENSORS.values())
    with db() as conn:
        devices = conn.execute("SELECT device FROM devices WHERE last_seen >= datetime('now', ?)",
                               (f"-{NODE_OFFLINE_ALERT_SECONDS} seconds",)).fetchall()
        for (device,) in devices:
            if device in state.NODE_OFFLINE:
                continue
            row = conn.execute(f"SELECT {has}, {fresh} FROM readings WHERE device = ?",
                               (*[silent_since] * len(SENSORS), device)).fetchone()
            # Was the node sending anything just before the silent window started?
            alive = conn.execute("""
                SELECT 1 FROM presence_readings WHERE device = ? AND ts BETWEEN datetime('now', ?) AND datetime('now', ?)
                UNION ALL
                SELECT 1 FROM readings WHERE device = ? AND ts BETWEEN datetime('now', ?) AND datetime('now', ?)
                LIMIT 1""", (device, alive_from, silent_since, device, alive_from, silent_since)).fetchone()
            for i, sensor in enumerate(SENSORS):
                if not row[i]:
                    continue   # the device has no such sensor (or no data in the window)
                ok = bool(row[len(SENSORS) + i])
                _sensor_state(device, sensor, silent=bool(alive) and not ok, back=ok)


def _sensor_state(device, sensor, silent, back):
    name = SENSORS[sensor][0]
    # One notification slot per sensor: the "back" push replaces the warning in the app
    excursion = Excursion(f"sensor_silent:{device}:{sensor}", "nodes", device,
                          {"event": "SENSOR_STATUS", "device": f"{device}:{sensor}"}, postpone_quiet=True)
    if silent:
        body = f"{device}: no {name} readings for {SENSOR_SILENT_SECONDS // 60} min"
        excursion.start(f"SENSOR_SILENT_{sensor.upper()}", body, "Sensor not reporting", body,
                        data={"status": "silent"})
    elif back:
        body = f"{device}: {name} readings are back"
        excursion.end(f"SENSOR_OK_{sensor.upper()}", body, "Sensor reporting again", body,
                      data={"status": "ok"})


def node_check_loop():
    while True:
        time.sleep(NODE_CHECK_INTERVAL)
        for check in (check_nodes, check_sensors):
            try:
                check()
            except Exception as e:
                log.error("%s failed: %s", check.__name__, e)


def handle_status(device, status):
    """home/esp32/<device>/status: "online" on connect, "offline" as the node's last will."""
    log.info("status %s: %s", device, status)
    log_event(device, "STATUS", status)
    if status == "online":
        touch_device(device)   # a sign of life; "offline" (sent by the broker) is not
