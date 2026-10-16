# #65: UTC storage, one-time migration, ISO 8601 API timestamps, local "today".
import os, sys, json, base64, tempfile, sqlite3, types, time, re
from datetime import datetime, timezone, timedelta
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for n in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[n] = mock.MagicMock()
sys.modules["firebase_admin"]._apps = {"x": 1}
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)

# Old DB with server-local timestamps (Budapest): one in summer time, one after the DST change
con = sqlite3.connect("data.db")
con.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, ts TIMESTAMP, temp REAL, humidity REAL, uptime INTEGER, co2 INTEGER, pressure REAL, co2_ok INTEGER)")
con.execute("CREATE TABLE devices (device TEXT PRIMARY KEY, last_seen TIMESTAMP)")
con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, ts TIMESTAMP, type TEXT, value TEXT)")
con.execute("INSERT INTO readings (device, ts, temp) VALUES ('a', '2026-10-15 20:00:00', 20), ('b', '2026-10-30 20:00:00', 21)")
con.execute("INSERT INTO devices VALUES ('a', '2026-10-15 20:00:00')")
con.execute("INSERT INTO events (device, ts, type, value) VALUES ('a', '2026-10-15 20:00:00', 'RESTART', 'x')")
con.commit(); con.close()

sys.path.insert(0, backend)
import builtins; _p = builtins.print
builtins.print = lambda *a, **k: None if a and str(a[0]).startswith(("[MQTT]", "[DEBUG]", "[PRESENCE]", "[DB]", "[STATUS]")) else _p(*a, **k)
import server
import config, state, store, nodes, ingest, api
server.prepare()   # importing does no work (#73)
db = lambda: sqlite3.connect(config.DB)
q = lambda sql: db().execute(sql).fetchall()

check("migration: summer row -2 h (CEST)", q("SELECT ts FROM readings WHERE device='a'")[0][0] == "2026-10-15 18:00:00")
check("migration: winter row -1 h (CET)", q("SELECT ts FROM readings WHERE device='b'")[0][0] == "2026-10-30 19:00:00")
check("migration: devices + events converted", q("SELECT last_seen FROM devices")[0][0] == "2026-10-15 18:00:00" and q("SELECT ts FROM events")[0][0] == "2026-10-15 18:00:00")
store.init_db()
check("second start doesn't convert again", q("SELECT ts FROM readings WHERE device='a'")[0][0] == "2026-10-15 18:00:00")
check("ts_utc marker set", store.get_state("ts_utc", None) == "1")

# New rows are UTC
mqtt = lambda topic, p: ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(p).encode()))
mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 45.0, "pressure": 1013.0, "uptime": 5})
stored = datetime.strptime(q("SELECT ts FROM readings WHERE device='home_ping_esp32'")[0][0], "%Y-%m-%d %H:%M:%S")
utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
check("new reading stored in UTC", abs((stored - utc_now).total_seconds()) < 5)

# API: ISO 8601 UTC
c = api.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
get = lambda path: c.get(path, headers=A).get_json()
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
l = get("/api/latest")
check("/api/latest ts is ISO UTC", bool(ISO.match(l["ts"])))
check("/api/latest ts parses to now", abs((datetime.strptime(l["ts"], "%Y-%m-%dT%H:%M:%SZ") - utc_now).total_seconds()) < 5)
check("/api/latest online (fresh, compared in UTC)", l["online"] is True)
check("/api/latest_all ts ISO", bool(ISO.match(get("/api/latest_all")["home_ping_esp32"]["ts"])))
devs = {d["device"]: d for d in get("/api/devices")}
check("/api/devices last_seen ISO, old device offline, new online", ISO.match(devs["a"]["last_seen"]) and devs["a"]["online"] is False and devs["home_ping_esp32"]["online"] is True)
check("/api/air ts ISO", bool(ISO.match(get("/api/air")["ts"])))
store.log_event("x", "RESTART", "uptime 100s -> 5s")
check("/api/restarts last_restart ISO", bool(ISO.match(get("/api/restarts")["x"]["last_restart"])))

# "today" stays local
h = get("/api/hourly")
local_hour = datetime.now().strftime("%Y-%m-%d %H:00")
check(f"/api/hourly label is local time ({local_hour})", h and h[0]["hour"] == local_hour)
old = db(); old.execute("INSERT INTO readings (device, ts, temp, humidity) VALUES ('home_ping_esp32', datetime('now','localtime','start of day','utc','-1 minutes'), 10, 10)"); old.commit(); old.close()
check("/api/hourly ignores readings from before local midnight", all(r["avg_temp"] != 10 for r in get("/api/hourly")))

# Offline check uses UTC
con = db(); con.execute("INSERT OR REPLACE INTO devices VALUES ('quiet', datetime('now','-400 seconds'))"); con.execute("INSERT OR REPLACE INTO devices VALUES ('fresh', datetime('now','-60 seconds'))"); con.commit(); con.close()
state.STARTED_AT = time.time() - 1000
nodes.check_nodes()
check("offline check: 400 s quiet -> offline, 60 s -> not", "quiet" in state.NODE_OFFLINE and "fresh" not in state.NODE_OFFLINE)
