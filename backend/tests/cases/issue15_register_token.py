# #15: register_token must not add a device; init_db removes an old "unknown" row.
import os, sys, base64, tempfile, sqlite3
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
db = server.DB
# Simulate an existing DB with the old phone row, then run init_db again (= backend restart)
con = sqlite3.connect(db)
con.execute("INSERT OR REPLACE INTO devices VALUES ('unknown', '2026-10-15 10:00:00')")
con.execute("INSERT OR REPLACE INTO devices VALUES ('home_ping_esp32', '2026-10-15 10:00:00')")
con.commit(); con.close()
server.init_db()
c = server.app.test_client()
auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
devices = lambda: [d["device"] for d in c.get("/api/devices", headers=auth).get_json()]
check("restart removes the old 'unknown' device", devices() == ["home_ping_esp32"])
r = c.post("/api/register_token", json={"token": "tok123"}, headers=auth)
check("register -> 200 success", r.status_code == 200 and r.get_json() == {"success": True})
r = c.post("/api/register_token", json={"token": "tok456", "device": "phone"}, headers=auth)
check("register with a device -> 200", r.status_code == 200)
check("register without token -> 400", c.post("/api/register_token", json={}, headers=auth).status_code == 400)
check("register adds no device", devices() == ["home_ping_esp32"])
calls = [cl.args for cl in server.messaging.subscribe_to_topic.call_args_list]
check("tokens subscribed to the env topic", calls == [(["tok123"], "env"), (["tok456"], "env")])
