# #82 history: /api/hourly?days=N, avg_co2/avg_pressure, validation.
import os, sys, base64, tempfile, sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m", TZ="Europe/Budapest")
for n in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[n] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
sys.path.insert(0, backend)
import builtins; _p = builtins.print
builtins.print = lambda *a, **k: None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[DB]", "[STATUS]")) else _p(*a, **k)
import server
server.prepare()   # importing does no work (#73)
con = sqlite3.connect(server.DB)
now = datetime.now(timezone.utc).replace(tzinfo=None)
fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
for days_ago in range(0, 8):
    t = fmt(now - timedelta(days=days_ago))
    con.execute("INSERT INTO readings (device, ts, temp, humidity) VALUES ('home_ping_esp32', ?, 20, 50)", (t,))
    con.execute("INSERT INTO readings (device, ts, co2, pressure) VALUES ('co2', ?, 800, 1010)", (t,))
    con.execute("INSERT INTO readings (device, ts, co2) VALUES ('co2', ?, 1000)", (t,))
    con.execute("INSERT INTO presence_readings (device, ts, occupied) VALUES ('home_ping_esp32', ?, 1)", (t,))
con.commit()
c = server.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
get = lambda path: c.get(path, headers=A)

h1 = get("/api/hourly").get_json()
check("default = today only: 1 row", len(h1) == 1)
r = h1[0]
check("fields", set(r) == {"hour", "avg_temp", "avg_humidity", "avg_co2", "avg_pressure", "avg_occupancy"})
check("co2 avg across devices = 900, pressure 1010", r["avg_co2"] == 900 and r["avg_pressure"] == 1010.0)
check("temp/hum/occ", r["avg_temp"] == 20 and r["avg_humidity"] == 50 and r["avg_occupancy"] == 100)
h7 = get("/api/hourly?days=7").get_json()
check("days=7: 7 rows (today + 6 days, 7-day-old row excluded)", len(h7) == 7)
check("newest first", h7[0]["hour"] > h7[-1]["hour"])
check("days=1 same as default", get("/api/hourly?days=1").get_json() == h1)
for bad in ["0", "8", "abc", "-1", "2.5"]:
    rv = get(f"/api/hourly?days={bad}")
    check(f"days={bad} -> 400 JSON error", rv.status_code == 400 and "error" in rv.get_json())
check("hourly_occupancy_today() default unchanged", len(server.hourly_occupancy_today()) == 1)
