# #58: a bad payload must not escape on_message (paho would re-raise it and end its network thread).
import os, sys, json, tempfile, sqlite3, types
from unittest import mock
import paho.mqtt.client as real_paho          # the real library, for its dispatch code
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
saved = {k: sys.modules[k] for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
for n in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[n] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
sys.path.insert(0, backend)
import builtins; _p = builtins.print; logged = []
builtins.print = lambda *a, **k: logged.append(" ".join(map(str, a)))
import server
server.prepare()   # importing does no work (#73)
sys.modules.update(saved)
def check(label, cond): _p(("PASS " if cond else "FAIL ") + label)

client = real_paho.Client(real_paho.CallbackAPIVersion.VERSION1)   # same as the backend
check("paho re-raises callback errors by default", client.suppress_exceptions is False)
client.on_message = server.on_message
def deliver(topic, payload):
    m = real_paho.MQTTMessage(topic=topic.encode()); m.payload = payload
    client._handle_on_message(m)          # paho's own dispatch path
rows = lambda: sqlite3.connect(server.DB).execute("SELECT COUNT(*) FROM readings").fetchone()[0]

for label, topic, payload in [
    ("invalid UTF-8 on a reading topic", "home/esp32/bme280", b"\xff\xfe"),
    ("invalid UTF-8 on a status topic", "home/esp32/x/status", b"\xffonline"),
    ("not JSON", "home/esp32/bme280", b"hello"),
    ("JSON but not an object", "home/esp32/bme280", b"[1,2,3]"),
    ("empty payload", "home/esp32/co2", b""),
]:
    try:
        deliver(topic, payload); check(f"{label}: handled inside the callback", True)
    except Exception as e:
        check(f"{label}: escaped to paho ({type(e).__name__})", False)
check("errors are logged", any(l.startswith("MQTT error:") for l in logged))
check("no row stored from bad payloads", rows() == 0)
deliver("home/esp32/bme280", json.dumps({"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 45.0, "uptime": 5}).encode())
check("next valid message is still stored", rows() == 1)
