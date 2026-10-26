"""In-memory runtime state shared by the modules (#73).

Other modules use it as `state.X`, so a reassignment (STARTED_AT, mqttc, ...) is
seen everywhere. server.prepare() resets it and loads the alert state that
survives restarts from the DB; start() sets STARTED_AT and mqttc.
"""

import time
import threading

LAST_UPTIME = {}        # device -> last reported uptime
RADAR_FAIL_COUNT = {}   # consecutive radar_connected=false messages (12 needed to alert)
LAST_NOTIFICATION = {}  # cooldown key -> unix time its cooldown started
PUSH_FAILURES = {}      # cooldown key -> failed sends in a row (retry back-off, #251)
RADAR_ALERTED = set()   # radars whose current outage has been handled
NODE_OFFLINE = {}       # device -> whether its offline push was sent (False: muted)
NODE_LOCK = threading.Lock()  # offline checks (own thread) vs. messages (MQTT thread)
STARTED_AT = time.time()      # reset by start(); the offline check and health count from it
LAST_MESSAGE_AT = None  # last live (non-retained) MQTT message, for /api/health (#77)
NODE_HEALTH = {}        # device -> newest presence health values + "at" (unix time), for /api/metrics
mqttc = None            # MQTT client, created by start()


def reset():
    """Back to a fresh process (prepare() calls it before loading the saved alert state)."""
    global STARTED_AT, LAST_MESSAGE_AT
    for d in (LAST_UPTIME, RADAR_FAIL_COUNT, LAST_NOTIFICATION, RADAR_ALERTED, NODE_OFFLINE, NODE_HEALTH):
        d.clear()
    STARTED_AT = time.time()
    LAST_MESSAGE_AT = None
