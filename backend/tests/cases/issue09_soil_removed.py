# #9: soil feature removed. Old DB keeps working, soil table dropped, endpoints gone.
import os, sys, json, base64, tempfile, sqlite3, types
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
server.prepare()   # importing does no work (#73)
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
def tables():
    con = sqlite3.connect(server.DB); t = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}; con.close(); return t
# Simulate an old DB that still has soil_readings, then restart
con = sqlite3.connect(server.DB)
con.execute("CREATE TABLE IF NOT EXISTS soil_readings (id INTEGER PRIMARY KEY, device TEXT, ts TIMESTAMP, moisture INTEGER, raw_value INTEGER)")
con.execute("INSERT INTO soil_readings (device, moisture, raw_value) VALUES ('soil_sensor_01', 40, 2000)")
con.commit(); con.close()
server.init_db()
check("soil_readings dropped on start", "soil_readings" not in tables())
check("other tables still there", {"readings", "presence_readings", "devices", "events", "system_state"} <= tables())
c = server.app.test_client()
auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
for path in ["/api/soil/latest", "/api/soil/history", "/api/soil/stats24h"]:
    check(f"{path} -> 404", c.get(path, headers=auth).status_code == 404)
check("/api/latest_all still 200", c.get("/api/latest_all", headers=auth).status_code == 200)
import builtins; _p = builtins.print
errors = []
builtins.print = lambda *a, **k: errors.append(a) if a and str(a[0]).startswith("MQTT error") else None
server.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/soil", payload=json.dumps({"device": "soil_sensor_01", "moisture": 10, "raw": 3000}).encode()))
builtins.print = _p
check("stray soil message: no handler error", not errors)
check("no soil push sent", not any("SOIL" in str(cl) for cl in server.messaging.send.call_args_list))
