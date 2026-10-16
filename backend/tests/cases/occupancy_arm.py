# Occupancy arm switch: POST /api/occupancy_arm and GET /api/occupancy_status.
import os, sys, types, base64, tempfile
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
c = server.app.test_client()
auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
check("status needs auth", c.get("/api/occupancy_status").status_code == 401)
check("disarmed at first", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": False})
check("arm -> armed", c.post("/api/occupancy_arm", json={"armed": True}, headers=auth).get_json() == {"armed": True})
check("status armed", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": True})
check("disarm -> disarmed", c.post("/api/occupancy_arm", json={"armed": False}, headers=auth).get_json() == {"armed": False})
check("status disarmed", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": False})
