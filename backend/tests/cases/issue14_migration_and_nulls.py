# #14 part 3: migration from the old readings layout, object responses, nulls, one confidence.
import os, sys, json, base64, tempfile, sqlite3, types
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging",
             "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)

# Old production layout with data, created BEFORE the server starts
con = sqlite3.connect("data.db")
con.execute("""CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, ts TIMESTAMP,
               temp REAL, humidity REAL, uptime INTEGER, occupied INTEGER, occupancy_confidence INTEGER,
               dark INTEGER, co2 INTEGER, pressure REAL)""")
con.execute("INSERT INTO readings (device, ts, temp, humidity, uptime, occupied, occupancy_confidence, dark, co2, pressure) "
            "VALUES ('home_ping_esp32', datetime('now','-1 minutes'), 22.5, 45.0, 100, 0, 0, 0, NULL, 1013.2)")
con.execute("INSERT INTO readings (device, ts, temp, humidity, uptime, occupied, occupancy_confidence, dark, co2, pressure) "
            "VALUES ('co2_sensor_esp32', datetime('now','-1 minutes'), NULL, NULL, 50, 0, 0, 0, 950, NULL)")
con.commit(); con.close()

sys.path.insert(0, backend)
import builtins; _p = builtins.print
import server
assert server.DB == "data.db", server.DB
builtins.print = lambda *a, **k: None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[DB]")) else _p(*a, **k)

cols = [r[1] for r in sqlite3.connect("data.db").execute("PRAGMA table_info(readings)")]
check("migration dropped occupied/occupancy_confidence/dark", not {"occupied", "occupancy_confidence", "dark"} & set(cols))
check("migration kept the other columns", cols == ["id", "device", "ts", "temp", "humidity", "uptime", "co2", "pressure", "co2_ok"])
check("migration kept the rows", sqlite3.connect("data.db").execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2)
server.init_db()   # second start: no-op
check("second start leaves layout alone", [r[1] for r in sqlite3.connect("data.db").execute("PRAGMA table_info(readings)")] == cols)

c = server.app.test_client()
A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
get = lambda p: c.get(p, headers=A).get_json()

# No radar data yet -> nulls
l = get("/api/latest")
check("/api/latest is an object", isinstance(l, dict))
check("/api/latest keys", set(l) == {"ts", "temp", "humidity", "uptime", "online", "occupied", "confidence"})
check("/api/latest values from migrated row", l["temp"] == 22.5 and l["humidity"] == 45.0)
check("no radar data: occupied/confidence null", l["occupied"] is None and l["confidence"] is None)
la = get("/api/latest_all")
check("latest_all env entry has no always-zero fields", not {"occupied", "occupancy_confidence", "dark"} & set(la["home_ping_esp32"]))
check("latest_all combined null without radar", la["combined"] == {"occupied": None, "confidence": None})
st = get("/api/stats24h_combined")
check("stats: no avg_confidence", "avg_confidence" not in st)
check("stats: occupancy null without radar data", st["avg_occupancy"] is None and st["occupied_hours"] is None)
check("stats: temp average from data", st["avg_temp"] == 22.5)
check("history_combined removed", c.get("/api/history_combined", headers=A).status_code == 404)

# Radar data arrives
def radar(dev, occ):
    server.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/presence", payload=json.dumps(
        {"device": dev, "radar_connected": True, "presence": occ, "stationary": occ, "moving": False,
         "distance": 100, "signal": 50 if occ else 0, "rssi": -60, "uptime": 10}).encode()))
for occ in (True, True, True, False):
    radar("home_ping_esp32", occ)
l = get("/api/latest")
check("occupied 1 with 2 of last 3 occupied", l["occupied"] == 1)
check("confidence 75 (3 of 4 rows in 15 min)", l["confidence"] == 75)
la = get("/api/latest_all")
check("radar entry uses 'confidence'", la["home_ping_esp32"].get("confidence") == 75 and "occupancy_confidence" not in la["home_ping_esp32"])
check("combined confidence == latest confidence", la["combined"]["confidence"] == l["confidence"])
h = get("/api/hourly")
check("hourly: no avg_confidence", all("avg_confidence" not in r for r in h))
check("hourly: occupancy for the current hour", h[0]["avg_occupancy"] == 75)
check("air band null without co2? (co2 present here)", get("/api/air")["band"] == "fair")
# env-only message after migration still stored
server.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/bme280", payload=json.dumps(
    {"device": "home_ping_esp32", "temp_c": 23.0, "humidity": 46.0, "uptime": 200}).encode()))
check("new env message stored and served", get("/api/latest")["temp"] == 23.0)

# Empty DB
os.remove("data.db"); server.init_db()
l = get("/api/latest")
check("empty DB: /api/latest 200 with nulls", l == {"ts": None, "temp": None, "humidity": None, "uptime": None, "online": False, "occupied": None, "confidence": None})
check("empty DB: stats nulls", all(get("/api/stats24h_combined")[k] is None for k in ("avg_temp", "avg_humidity", "avg_occupancy")))
check("empty DB: air band null", get("/api/air")["band"] is None)
