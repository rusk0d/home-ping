"""Device heartbeat, node offline / back online alerts (#13), silent sensors (#224).
"""

import logging
import time

import state
from config import (
    DEVICES_RETENTION_DAYS, NODE_CHECK_INTERVAL, NODE_OFFLINE_ALERT_SECONDS,
    NOTIFICATION_COOLDOWN, SENSOR_SILENT_SECONDS,
)
from store import db, get_state, log_event, parse_ts, set_state, utcnow
from alerts import alert, alert_muted

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
        # Forget nodes that have been gone for a long time (e.g. a retired node)
        c.execute("DELETE FROM devices WHERE last_seen < datetime('now',?)",
                  (f"-{DEVICES_RETENTION_DAYS} days",))
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
            if time.time() - state.LAST_NOTIFICATION.get(f"NODE_{device}", 0) <= NOTIFICATION_COOLDOWN:
                continue
            result = alert(device, "NODE_OFFLINE", f"last seen {last_seen}",
                           title="Node offline",
                           body=f"{device}: no data for {int(age // 60)} min",
                           data={"event": "NODE_STATUS", "status": "offline", "device": device},
                           cooldown_key=f"NODE_{device}", kind="nodes")
            if result:
                set_node_offline(device, True, pushed=result == "sent")


def node_back_online(device):
    with state.NODE_LOCK:
        if device not in state.NODE_OFFLINE:
            return
        pushed = state.NODE_OFFLINE[device]
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
        state.NODE_OFFLINE[device] = pushed
    else:
        state.NODE_OFFLINE.pop(device, None)
    set_state(f"node_offline:{device}", ("1" if pushed else "m") if offline else "0")


# -------------------------
# SENSOR SILENT / BACK (#224)
# -------------------------
# A node can stay online (radar, status, the other sensor keep last_seen fresh) while one
# of its sensors is dead: a failed BME280 publishes nothing, a failed MH-Z19C publishes
# co2_ok false. Checked for each sensor a device has in the raw data (kept
# RAW_RETENTION_DAYS): silent when there's been no valid value for SENSOR_SILENT_SECONDS
# although the node was sending other data at the start of that time and still is, so an
# outage (NODE_STATUS) or a reboot doesn't count. Once per excursion, persisted as
# sensor_silent:<device>:<sensor> ('1' pushed, 'm' muted, '0' fine); quiet hours postpone
# it, like the server watchdog.
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
    key = f"sensor_silent:{device}:{sensor}"
    status = get_state(key, "0")
    # One notification slot per sensor: the "back" push replaces the warning in the app
    data = {"event": "SENSOR_STATUS", "device": f"{device}:{sensor}"}
    if status == "0" and silent:
        if alert_muted("nodes") == "quiet hours":
            return   # try again after quiet hours
        body = f"{device}: no {name} readings for {SENSOR_SILENT_SECONDS // 60} min"
        result = alert(device, f"SENSOR_SILENT_{sensor.upper()}", body, "Sensor not reporting", body,
                       {**data, "status": "silent"}, kind="nodes")
        if result:   # a failed send is retried at the next check
            set_state(key, "1" if result == "sent" else "m")
    elif status != "0" and back:
        set_state(key, "0")
        body = f"{device}: {name} readings are back"
        if status == "1":   # only clear a notification that was actually shown
            alert(device, f"SENSOR_OK_{sensor.upper()}", body, "Sensor reporting again", body,
                  {**data, "status": "ok"}, kind="nodes")
        else:
            log_event(device, f"SENSOR_OK_{sensor.upper()}", f"no push (warning was muted), {body}")


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
