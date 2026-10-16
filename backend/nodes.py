"""Device heartbeat and node offline / back online alerts (#13).
"""

import time
from datetime import datetime

import state
from config import (
    DEVICES_RETENTION_DAYS, NODE_CHECK_INTERVAL, NODE_OFFLINE_ALERT_SECONDS,
    NOTIFICATION_COOLDOWN,
)
from store import db, log_event, set_state, utcnow
from alerts import alert


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
        age = (now - datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")).total_seconds()
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
