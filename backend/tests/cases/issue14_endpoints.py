# #14 part 1: dead endpoints gone, used ones still answer.
import os, sys, base64, tempfile
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging",
             "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
sys.path.insert(0, backend)
import server   # no google/requests stubs: the import must work without them
c = server.app.test_client()
auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
for p in ["/api/latest2", "/api/history", "/api/stats24h", "/api/occupancy_rolling",
          "/api/presence/latest", "/api/presence/history", "/api/widget", "/api/next-events"]:
    check(f"{p} removed (404)", c.get(p, headers=auth).status_code == 404)
for p in ["/api/latest", "/api/latest_all", "/api/devices", "/api/stats24h_combined", "/api/restarts",
          "/api/air", "/api/hourly", "/api/occupancy_status"]:
    r = c.get(p, headers=auth)
    check(f"{p} 200", r.status_code == 200)
check("POST occupancy_arm 200", c.post("/api/occupancy_arm", json={"armed": True}, headers=auth).status_code == 200)
check("POST register_token 200", c.post("/api/register_token", json={"token": "t"}, headers=auth).status_code == 200)
check("auth still required", c.get("/api/latest").status_code == 401)
