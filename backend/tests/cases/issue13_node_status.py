# #13: status topic, node offline / back online pushes, persisted across restarts.
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
errors = []
builtins.print = lambda *a, **k: errors.append(a) if a and str(a[0]).startswith("MQTT error") else (None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[ERROR]", "[DB]", "[STATUS]")) else _p(*a, **k))
import server
server.prepare()   # importing does no work (#73)
m = server.messaging
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
def status_pushes():
    return [c.kwargs["data"]["status"] for c in m.Message.call_args_list if c.kwargs.get("data", {}).get("event") == "NODE_STATUS"]
def mqtt(topic, payload, retain=False):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    server.on_message(None, None, types.SimpleNamespace(retain=retain, topic=topic, payload=raw.encode()))
db = lambda: sqlite3.connect(server.DB)
def set_seen(dev, ago_s):
    con = db(); con.execute("INSERT OR REPLACE INTO devices VALUES (?, datetime('now',?))", (dev, f"-{ago_s} seconds")); con.commit(); con.close()
def restart():
    global server, m
    server = importlib.reload(server); server.prepare(); m = server.messaging; m.Message.reset_mock(); m.send.reset_mock()
def past_grace(): server.STARTED_AT = time.time() - server.NODE_OFFLINE_ALERT_SECONDS - 1

# status topic
mqtt("home/esp32/co2_sensor_esp32/status", "online")
check("status 'online': no MQTT error (not parsed as JSON)", not errors)
check("status 'online': logged as STATUS event", db().execute("SELECT value FROM events WHERE type='STATUS'").fetchall() == [("online",)])
check("status 'online': counts as a sign of life", db().execute("SELECT COUNT(*) FROM devices WHERE device='co2_sensor_esp32'").fetchone()[0] == 1)
check("status: no readings row", db().execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0)
set_seen("co2_sensor_esp32", 600)
mqtt("home/esp32/co2_sensor_esp32/status", "offline")
check("status 'offline' (last will): last_seen not refreshed", db().execute(
    "SELECT last_seen < datetime('now','-500 seconds') FROM devices WHERE device='co2_sensor_esp32'").fetchone()[0] == 1)
mqtt("home/esp32/co2_sensor_esp32/status", "offline", retain=True)
check("retained status replay skipped", db().execute("SELECT COUNT(*) FROM events WHERE type='STATUS'").fetchone()[0] == 2)

# offline detection
set_seen("home_ping_esp32", 400)       # > 300 s
set_seen("hallway_presence_esp32", 60) # fine
server.check_nodes()
check("startup grace: no push right after a restart", status_pushes() == [])
past_grace(); server.check_nodes()
pushed = status_pushes()
check("offline push for the 2 silent nodes only", sorted(pushed) == ["offline", "offline"] and "hallway_presence_esp32" not in server.NODE_OFFLINE)
check("offline flag persisted", server.get_state("node_offline:home_ping_esp32") == "1")
server.check_nodes()
check("no repeat while still offline", len(status_pushes()) == 2)
restart(); past_grace()
check("after restart: offline flags loaded", {"home_ping_esp32", "co2_sensor_esp32"} <= server.NODE_OFFLINE.keys())
server.check_nodes()
check("after restart: no repeat push", len(status_pushes()) == 0)   # new mock after reload

# back online
mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 45.0, "uptime": 5})
check("data again -> 'online' push", status_pushes() == ["online"])
check("flag cleared", "home_ping_esp32" not in server.NODE_OFFLINE and server.get_state("node_offline:home_ping_esp32") == "0")
data = [c.kwargs["data"] for c in m.Message.call_args_list][-1]
check("online push uses the same event/device as offline (same app slot)", data["event"] == "NODE_STATUS" and data["device"] == "home_ping_esp32")
check("events logged", {r[0] for r in db().execute("SELECT type FROM events WHERE type LIKE 'NODE_%'")} == {"NODE_OFFLINE", "NODE_ONLINE"})

# failed offline push is retried after 60 s, not lost
set_seen("eink_test", 400)
m.send.side_effect = Exception("FCM down"); server.check_nodes(); m.send.side_effect = None
check("failed offline push: flag not set", "eink_test" not in server.NODE_OFFLINE)
server.check_nodes()
check("...not retried immediately", "eink_test" not in server.NODE_OFFLINE)
server.set_cooldown("NODE_eink_test", time.time() - server.NOTIFICATION_COOLDOWN - 1)
server.check_nodes()
check("...retried after the retry window", "eink_test" in server.NODE_OFFLINE)
check("no MQTT errors overall", not errors)
