"""Entry point: `python3 server.py` (systemd unit iot-backend).

Importing the modules does no work (#73): prepare() sets up the database,
start() also connects Firebase and MQTT and starts the background threads.
"""

import time
import threading
import paho.mqtt.client as mqtt
import firebase_admin
from firebase_admin import credentials

import state
from config import FIREBASE_CRED, MQTT_HOST, MQTT_PASS, MQTT_PORT, MQTT_USER
from store import init_db, load_alert_state
from nodes import node_check_loop
from ingest import hourly_occupancy_loop, on_connect, on_disconnect, on_message
from api import app


def init_firebase():
    try:
        firebase_admin.get_app()   # already initialised (e.g. start() called twice)
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED))


def prepare():
    """Create/migrate the DB schema and load the alert state that survives restarts (#5)."""
    init_db()
    state.reset()
    cooldowns, alerted, offline = load_alert_state()
    state.LAST_NOTIFICATION.update(cooldowns)
    state.RADAR_ALERTED.update(alerted)
    state.NODE_OFFLINE.update(offline)


def start():
    """Everything the running backend needs except the web server itself."""
    prepare()
    init_firebase()
    state.STARTED_AT = time.time()
    state.mqttc = mqtt.Client()
    state.mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
    state.mqttc.on_connect = on_connect
    state.mqttc.on_message = on_message
    state.mqttc.on_disconnect = on_disconnect
    state.mqttc.connect(MQTT_HOST, MQTT_PORT)
    state.mqttc.loop_start()
    threading.Thread(target=hourly_occupancy_loop, daemon=True).start()
    threading.Thread(target=node_check_loop, daemon=True).start()


def main():
    start()
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
