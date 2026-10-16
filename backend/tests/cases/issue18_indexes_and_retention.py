# #18: indexes exist, statistics gathered, events/devices retention.
import os, sys, base64, tempfile, sqlite3
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
sys.path.insert(0, backend)
import server
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
db = lambda: sqlite3.connect(server.DB)
idx = {r[0] for r in db().execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")}
check("5 indexes created", idx == {"ix_presence_device_id", "ix_presence_device_ts", "ix_readings_device_id", "ix_readings_device_ts", "ix_events_type_ts"})
check("ANALYZE ran (sqlite_stat1 exists)", db().execute("SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()[0] == 1)
server.init_db()
check("second start is fine (IF NOT EXISTS)", len({r[0] for r in db().execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")}) == 5)
con = db()
con.execute("INSERT INTO events (device, ts, type, value) VALUES ('x', datetime('now','-31 days'), 'RESTART', 'old')")
con.execute("INSERT INTO events (device, ts, type, value) VALUES ('x', datetime('now','-29 days'), 'RESTART', 'keep')")
con.execute("INSERT INTO devices VALUES ('retired_node', datetime('now','-31 days'))")
con.execute("INSERT INTO devices VALUES ('quiet_node', datetime('now','-29 days'))")
con.commit(); con.close()
server.log_event("home_ping_esp32", "OCCUPANCY_CHANGE", "occupied")
vals = [r[0] for r in db().execute("SELECT value FROM events WHERE device='x'")]
check("event older than 30 days deleted, 29 days kept", vals == ["keep"])
server.touch_device("home_ping_esp32")
devs = {r[0] for r in db().execute("SELECT device FROM devices")}
check("device unseen 31 days removed, 29 days kept", "retired_node" not in devs and "quiet_node" in devs and "home_ping_esp32" in devs)
c = server.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
check("/api/devices lists the remaining ones", {d["device"] for d in c.get("/api/devices", headers=A).get_json()} == devs)
