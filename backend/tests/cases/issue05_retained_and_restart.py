# #5: retained messages skipped; cooldowns and radar-outage flags survive a restart.
import os, sys, json, tempfile, sqlite3, types, importlib, time
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
sys.path.insert(0, backend)
import builtins; _p = builtins.print
builtins.print = lambda *a, **k: None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[ERROR]", "[DB]")) else _p(*a, **k)
import server
server.prepare()   # importing does no work (#73)
m = server.messaging   # what server.py actually calls
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
def pushes(event): return sum(1 for c in m.send.call_args_list if m.Message.call_args_list and True) and \
    sum(1 for c in m.Message.call_args_list if c.kwargs.get("data", {}).get("event") == event)
def restart():
    global server
    server = importlib.reload(server); server.prepare()
def mqtt(topic, p, retain=False):
    server.on_message(None, None, types.SimpleNamespace(retain=retain, topic=topic, payload=json.dumps(p).encode()))
rows = lambda t: sqlite3.connect(server.DB).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
env = {"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 70.0, "uptime": 100}

# 1. retained replay is ignored completely
mqtt("home/esp32/bme280", env, retain=True)
check("retained: no readings row", rows("readings") == 0)
check("retained: device not touched", rows("devices") == 0)
check("retained: no push", pushes("HUMIDITY_HIGH") == 0)

# 2. cooldown survives a restart
mqtt("home/esp32/bme280", env)
check("live: row stored", rows("readings") == 1)
check("live: humidity push sent", pushes("HUMIDITY_HIGH") == 1)
check("cooldown persisted in system_state", sqlite3.connect(server.DB).execute(
    "SELECT COUNT(*) FROM system_state WHERE key='cooldown:HUMIDITY_home_ping_esp32'").fetchone()[0] == 1)
restart()
check("after restart: cooldown loaded", "HUMIDITY_home_ping_esp32" in server.LAST_NOTIFICATION)
mqtt("home/esp32/bme280", env)
check("after restart: no repeat push within cooldown", pushes("HUMIDITY_HIGH") == 1)
mqtt("home/esp32/bme280", env, retain=True)
check("after restart: retained replay still ignored", rows("readings") == 2)

# 3. radar outage flag survives a restart
radar = lambda ok: mqtt("home/esp32/presence", {"device": "hallway_presence_esp32", "radar_connected": ok, "presence": False,
    "stationary": False, "moving": False, "distance": 0, "signal": 0, "rssi": -70, "uptime": 50})
for _ in range(12): radar(False)
check("outage: one push", pushes("RADAR_DISCONNECTED") == 1)
restart()
check("after restart: outage flag loaded", "hallway_presence_esp32" in server.RADAR_ALERTED)
server.set_cooldown("RADAR_hallway_presence_esp32", 0)   # cooldown long over
for _ in range(15): radar(False)
check("after restart: same outage not pushed again", pushes("RADAR_DISCONNECTED") == 1)
radar(True)
check("radar back: flag cleared in DB", server.get_state("radar_alerted:hallway_presence_esp32") == "0")
restart()
check("after restart: cleared flag stays cleared", "hallway_presence_esp32" not in server.RADAR_ALERTED)
for _ in range(12): radar(False)
check("new outage: pushed again", pushes("RADAR_DISCONNECTED") == 2)

# 4. failed send: short retry survives a restart too
m.send.side_effect = Exception("FCM down")
mqtt("home/esp32/bme280", {"device": "t_node", "temp_c": 15.0, "uptime": 10})
m.send.side_effect = None
restart()
left = server.LAST_NOTIFICATION["TEMP_t_node"] + server.NOTIFICATION_COOLDOWN - time.time()
check(f"failed push: retry in ~60 s kept across restart (got {left:.0f} s)", 55 <= left <= 61)
