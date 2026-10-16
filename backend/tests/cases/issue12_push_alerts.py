# #12 backend: data-only pushes, failed sends, temp/humidity independent, radar once per outage.
# Imports backend/server.py with Firebase/MQTT stubbed, against a temp DB.
import os, sys, json, tempfile, sqlite3, types
from unittest import mock

backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging",
             "paho", "paho.mqtt", "paho.mqtt.client", "google", "google.oauth2",
             "google.oauth2.credentials", "google.auth", "google.auth.transport",
             "google.auth.transport.requests", "requests"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
sys.path.insert(0, backend)
import server

m = server.messaging
sent = []        # (kwargs of each Message) for accepted sends


def fake_message(**kw):
    return kw


m.Message.side_effect = fake_message
m.AndroidConfig.side_effect = lambda **kw: ("AndroidConfig", kw)


def ok_send(msg):
    sent.append(msg)
    return "projects/x/messages/1"


m.send.side_effect = ok_send


def mqtt(topic, payload):
    server.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(payload).encode()))


def events(t):
    con = sqlite3.connect(server.DB)
    rows = con.execute("SELECT type, value FROM events WHERE type = ? ORDER BY id", (t,)).fetchall()
    con.close()
    return rows


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)


import builtins
_print = builtins.print
builtins.print = lambda *a, **k: None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[ERROR]")) else _print(*a, **k)

# 1. Data-only format
mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 70.0, "uptime": 10})
msg = sent[-1]
check("message has no notification block", "notification" not in msg)
check("title/body/event in data", msg["data"].get("title") == "Humidity Alert" and msg["data"].get("event") == "HUMIDITY_HIGH" and "body" in msg["data"])
check("all data values are strings", all(isinstance(v, str) for v in msg["data"].values()))
check("android priority high", msg.get("android") == ("AndroidConfig", {"priority": "high"}))
check("event logged as sent", events("HUMIDITY_HIGH_ALERT")[-1][1].startswith("sent, "))

# 2. Temperature alone (no humidity) still alerts; humidity alone too
n = len(sent)
mqtt("home/esp32/bme280", {"device": "temp_only_node", "temp_c": 16.5, "uptime": 10})
check("temp-only message sends TEMP alert", len(sent) == n + 1 and sent[-1]["data"]["event"] == "TEMP")
mqtt("home/esp32/bme280", {"device": "hum_only_node", "humidity": 20.0, "uptime": 10})
check("humidity-only message sends HUMIDITY_LOW", sent[-1]["data"]["event"] == "HUMIDITY_LOW")

# 3. Cooldown 15 min, and a failed send retries after 60 s instead of 15 min
check("NOTIFICATION_COOLDOWN is 900 s", server.NOTIFICATION_COOLDOWN == 900)
m.send.side_effect = Exception("FCM down")
mqtt("home/esp32/bme280", {"device": "fail_node", "temp_c": 15.0, "uptime": 10})
check("failed send logged as failed", events("TEMP_ALERT")[-1] == ("TEMP_ALERT", "failed, temp=15.0"))
import time
left = server.LAST_NOTIFICATION["TEMP_fail_node"] + server.NOTIFICATION_COOLDOWN - time.time()
check(f"retry scheduled in ~60 s (got {left:.0f} s)", 55 <= left <= 61)
m.send.side_effect = ok_send
server.LAST_NOTIFICATION["TEMP_fail_node"] -= 61   # 61 s later
n = len(sent)
mqtt("home/esp32/bme280", {"device": "fail_node", "temp_c": 15.0, "uptime": 20})
check("retried after 60 s and sent", len(sent) == n + 1 and events("TEMP_ALERT")[-1][1].startswith("sent, "))

# 4. Radar disconnected: once per outage
def radar(connected):
    mqtt("home/esp32/presence", {"device": "hallway_presence_esp32", "radar_connected": connected,
                                 "presence": False, "stationary": False, "moving": False,
                                 "distance": 0, "signal": 0, "rssi": -60, "uptime": 100})

def radar_pushes():
    return sum(1 for s in sent if s["data"].get("event") == "RADAR_DISCONNECTED")

for _ in range(30):
    radar(False)
check("one push after 30 disconnected messages", radar_pushes() == 1)
server.LAST_NOTIFICATION["RADAR_hallway_presence_esp32"] = 0   # cooldown long over
for _ in range(5):
    radar(False)
check("no repeat while still down, even after the cooldown", radar_pushes() == 1)
radar(True)
for _ in range(12):
    radar(False)
check("new outage after reconnect alerts again", radar_pushes() == 2)

# 5. Occupancy arrival goes through alert() too
server.set_state("occupancy_armed", "1") if hasattr(server, "set_state") else None
con = sqlite3.connect(server.DB); con.execute("UPDATE system_state SET value='1' WHERE key='occupancy_armed'"); con.commit(); con.close()
mqtt("home/esp32/presence", {"device": "home_ping_esp32", "radar_connected": True, "presence": True,
                             "stationary": False, "moving": True, "distance": 150, "signal": 60, "rssi": -60, "uptime": 100})
occ = [s for s in sent if s["data"].get("event") == "OCCUPIED"]
check("occupancy push sent with data-only format", len(occ) == 1 and occ[0]["data"]["title"] == "Occupancy Alert" and "notification" not in occ[0])
check("OCCUPANCY_ALERT logged as sent", events("OCCUPANCY_ALERT")[-1][1].startswith("sent, after"))
