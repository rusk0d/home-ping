"""Entry point: `python3 server.py` (systemd unit iot-backend).

Importing the modules does no work (#73): prepare() sets up the database,
start() also connects Firebase and MQTT and starts the background threads.
"""

import logging
import time
import threading
import paho.mqtt.client as mqtt
from waitress import serve
import firebase_admin
from firebase_admin import credentials

import state
from config import BIND_HOST, PORT, LOG_LEVEL, FIREBASE_CRED, FIREBASE_TIMEOUT, MQTT_HOST, MQTT_PASS, MQTT_PORT, MQTT_USER
from store import get_state, load_alert_state, prune, rollup_hourly
from schema import init_db
from nodes import node_check_loop
from ingest import summaries_loop, on_connect, on_connect_fail, on_disconnect, on_message
from air import air_loop
from api import app

log = logging.getLogger(__name__)


def init_firebase():
    try:
        firebase_admin.get_app()   # already initialised (e.g. start() called twice)
    except ValueError:
        # httpTimeout: a push that hangs must not hold the MQTT thread for long (#449)
        firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED), {"httpTimeout": FIREBASE_TIMEOUT})


def prepare():
    """Create/migrate the DB schema and load the alert state that survives restarts (#5)."""
    init_db()
    n = rollup_hourly()   # catches up after downtime (before new rows prune the raw tables)
    if n:
        log.info("hourly rollup: %d hours", n)
    prune()   # after the catch-up rollup (#304)
    state.reset()
    cooldowns, alerted, offline = load_alert_state()
    state.LAST_NOTIFICATION.update(cooldowns)
    state.RADAR_ALERTED.update(alerted)
    state.NODE_OFFLINE.update(offline)
    state.HOME_OFFLINE = get_state("home_offline", "") in ("sent", "muted")


def start():
    """Everything the running backend needs except the web server itself."""
    # journald adds the time; a no-op if logging was configured already (e.g. by a WSGI server)
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")
    prepare()
    init_firebase()
    state.STARTED_AT = time.time()
    state.mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)   # v1 is deprecated (#66)
    state.mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
    state.mqttc.on_connect = on_connect
    state.mqttc.on_message = on_message
    state.mqttc.on_disconnect = on_disconnect
    state.mqttc.on_connect_fail = on_connect_fail
    # connect_async: the connection is made by paho's own thread, which also retries it. A
    # broker that isn't up yet (both start at boot) is then a warning per attempt, not a
    # crash with a traceback and a systemd restart every 5 s. Retries back off from 1 to 30 s
    # (paho's default goes up to 120 s), for the first connection and every later reconnect.
    state.BROKER_UP = False   # until on_connect: no node check before the first connection (#476)
    state.mqttc.reconnect_delay_set(min_delay=1, max_delay=30)
    state.mqttc.connect_async(MQTT_HOST, MQTT_PORT)
    state.mqttc.loop_start()
    threading.Thread(target=summaries_loop, daemon=True).start()
    threading.Thread(target=node_check_loop, daemon=True).start()
    threading.Thread(target=air_loop, daemon=True).start()   # #245


def main():
    start()
    # waitress, not Flask's development server (#59): one process with a thread pool,
    # so the MQTT client and the background threads above live next to the API.
    # Bound to localhost by default; nginx is the only public entry point.
    serve(app, host=BIND_HOST, port=PORT, threads=4)


if __name__ == "__main__":
    main()
