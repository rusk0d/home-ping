# #77: /api/health
import os, sys, json, tempfile, types, time
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
server.init_db()
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
c = server.app.test_client()
def get():
    r = c.get("/api/health"); return r.status_code, r.get_json()
def msg(retain=False):
    server.on_message(None, None, types.SimpleNamespace(retain=retain, topic="home/esp32/presence",
        payload=json.dumps({"device": "home_ping_esp32", "presence": False, "moving": False,
                            "stationary": False, "signal": 0, "distance": 0}).encode()))

server.mqttc.is_connected = lambda: True
s, b = get()
check("just started, no message yet -> 200", s == 200 and b["ok"] and b["last_message_age_s"] is None)
check("no auth needed, no sensor data", set(b) == {"ok", "mqtt_connected", "last_message_age_s", "db_ok"})

server.STARTED_AT = time.time() - 400
s, b = get()
check("no message 400 s after start -> 503", s == 503 and not b["ok"])

msg(retain=True)
check("retained replay doesn't count", get()[0] == 503)
msg()
s, b = get()
check("live message -> 200, age 0", s == 200 and b["last_message_age_s"] == 0)

server.LAST_MESSAGE_AT = time.time() - 301
s, b = get()
check("last message 301 s ago -> 503", s == 503 and b["last_message_age_s"] == 301)
msg()

server.mqttc.is_connected = lambda: False
s, b = get()
check("MQTT disconnected -> 503", s == 503 and b["mqtt_connected"] is False)
server.mqttc.is_connected = lambda: True

real = server.DB
server.DB = os.path.join("nope", "x.db")
s, b = get()
check("DB unreadable -> 503", s == 503 and b["db_ok"] is False)
server.DB = real
check("back to 200", get()[0] == 200)
check("other endpoints still need auth", c.get("/api/latest").status_code == 401)
