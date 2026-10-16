# Arrival alert: first reading after the quiet spell is stationary, movement follows.
import os, sys, tempfile, sqlite3, time
from datetime import timedelta
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
import config, store, alerts, ingest
server.prepare()   # importing does no work (#73)
store.init_db()
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)

pushes = []
alerts.send_push = lambda title, body, data: pushes.append(body) or True

def reading(device, moving=False, stationary=False, signal=0, distance=100):
    ingest.handle_presence_data({"device": device, "presence": moving or stationary,
        "moving": moving, "stationary": stationary, "signal": signal, "distance": distance,
        "rssi": -60, "uptime": 1000, "radar_connected": True})

def age_rows(minutes):
    """Pretend every logged reading happened [minutes] earlier."""
    con = sqlite3.connect(config.DB)
    for (i, ts) in con.execute("SELECT id, ts FROM presence_readings").fetchall():
        old = store.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=minutes)
        con.execute("UPDATE presence_readings SET ts=? WHERE id=?", (old.strftime("%Y-%m-%d %H:%M:%S"), i))
    con.commit(); con.close()

def leave_for(minutes):
    """Someone was home, then both radars report empty for [minutes]."""
    reading("home_ping_esp32", moving=True, signal=100)
    reading("hallway_presence_esp32")
    reading("home_ping_esp32")
    age_rows(minutes)

store.set_state("occupancy_armed", "1")

# 1. The real case: 70 min away, the hallway first sees a still target, then movement
leave_for(70)
pushes.clear()   # fresh DB: the very first reading is itself an "arrival"
reading("hallway_presence_esp32", stationary=True, signal=100, distance=137)
check("no push on the stationary first reading", pushes == [])
reading("home_ping_esp32")   # other radar, empty, in between
reading("hallway_presence_esp32", moving=True, stationary=True, signal=100, distance=159)
check("push once movement follows", len(pushes) == 1)
check("body has the quiet spell (70 min)", pushes and "after 70 min of quiet" in pushes[0] and "hallway" in pushes[0])
reading("hallway_presence_esp32", moving=True, signal=100, distance=73)
reading("home_ping_esp32", moving=True, signal=100, distance=300)
check("only once per arrival", len(pushes) == 1)

# 2. Moving on the first reading still alerts right away
pushes.clear()
leave_for(90)
reading("home_ping_esp32", moving=True, signal=100, distance=200)
check("moving first reading -> push", len(pushes) == 1 and "main room" in pushes[0])

# 3. Stationary ghost after quiet, no movement within 120 s -> no push
pushes.clear()
leave_for(90)
reading("home_ping_esp32", stationary=True, signal=40, distance=370)
check("ghost: no push yet", pushes == [])
store.set_state("arrival_started", str(time.time() - 200))   # window expired
reading("home_ping_esp32", moving=True, signal=100, distance=200)
check("movement after the window -> no push", pushes == [])

# 4. Short absence -> no push
pushes.clear()
leave_for(30)
reading("hallway_presence_esp32", stationary=True, signal=100)
reading("hallway_presence_esp32", moving=True, signal=100)
check("30 min quiet (< 60) -> no push", pushes == [])

# 5. Disarmed: the arrival is consumed but nothing is pushed
pushes.clear()
store.set_state("occupancy_armed", "0")
leave_for(90)
reading("hallway_presence_esp32", stationary=True, signal=100)
reading("hallway_presence_esp32", moving=True, signal=100)
check("disarmed -> no push", pushes == [])
con = sqlite3.connect(config.DB)
ev = con.execute("SELECT value FROM events WHERE type='OCCUPANCY_ALERT'").fetchall()
con.close()
check("events logged for the 3 pushes", len(ev) == 3 and all(v[0].startswith("sent, after") for v in ev))
