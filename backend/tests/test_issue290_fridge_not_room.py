"""#290: the fridge's temperature isn't the room's: no room temperature push, left out of
the hourly roll-up and /api/latest; the one-time hourly rebuild after the fix."""
import base64
import json
import types
from datetime import timedelta

from support import check


def test_issue290_fridge_not_room(env):
    import server, api, ingest, alerts
    from store import db, init_db, rollup_hourly, utcnow
    server.prepare()

    def q(sql, *args):
        with db() as conn:
            return conn.execute(sql, args).fetchall()

    pushes = []
    alerts.messaging.send = lambda m: pushes.append(m) or "id"
    alerts.messaging.Message = lambda **kw: kw
    mqtt = lambda topic, p: ingest.on_message(None, None, types.SimpleNamespace(
        retain=False, topic=topic, payload=json.dumps(p).encode()))

    # --- no room "temperature is low" push for a 3.5 °C fridge
    mqtt("home/esp32/fridge_temp", {"device": "fridge_temp_esp32", "temp_c": 3.5, "alert": "", "uptime": 60})
    check("fridge reading stored", q("SELECT temp FROM readings WHERE device='fridge_temp_esp32'") == [(3.5,)])
    check("no room temperature push for the fridge", not any(
        p.get("data", {}).get("event") == "TEMP" for p in pushes))
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 15.0, "humidity": 45, "uptime": 60})
    check("the room's own low temperature still pushes", any(
        p.get("data", {}).get("event") == "TEMP" for p in pushes))

    # --- /api/latest: the room, even when the fridge is newer
    mqtt("home/esp32/fridge_temp", {"device": "fridge_temp_esp32", "temp_c": 4.0, "alert": "", "uptime": 120})
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    latest = api.app.test_client().get("/api/latest", headers=auth).get_json()
    check("/api/latest is the room", latest["temp"] == 15.0)

    # --- hourly roll-up without the fridge, and the one-time rebuild of polluted hours
    hour = utcnow().replace(minute=0, second=0, microsecond=0)
    t = lambda h_ago, m: (hour - timedelta(hours=h_ago, minutes=-m)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute("DELETE FROM readings")
        conn.executemany("INSERT INTO readings (device, ts, temp) VALUES (?, ?, ?)", [
            ("home_ping_esp32", t(3, 10), 22.0), ("fridge_temp_esp32", t(3, 20), 4.0),
            ("home_ping_esp32", t(2, 10), 21.0), ("fridge_temp_esp32", t(2, 20), 4.0),
        ])
        # what the old roll-up wrote: room and fridge averaged together
        conn.execute("DELETE FROM hourly")
        conn.executemany("INSERT INTO hourly (hour, temp_avg, temp_min) VALUES (?, ?, ?)",
                         [(t(3, 0), 13.0, 4.0), (t(2, 0), 12.5, 4.0)])
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('hourly_until', ?)", (t(0, 0),))
        conn.execute("DELETE FROM system_state WHERE key = 'hourly_room_rebuilt'")
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('env_alerted:TEMP_fridge_temp_esp32', 'low')")

    init_db()
    rollup_hourly()
    rows = {r[0]: r[1:] for r in q("SELECT hour, temp_avg, temp_min FROM hourly")}
    check("the latched fridge temperature alert is cleared",
          q("SELECT 1 FROM system_state WHERE key = 'env_alerted:TEMP_fridge_temp_esp32'") == [])
    check("polluted hours rebuilt without the fridge", rows[t(3, 0)] == (22.0, 22.0) and rows[t(2, 0)] == (21.0, 21.0))

    with db() as conn:
        conn.execute("UPDATE hourly SET temp_avg = 99 WHERE hour = ?", (t(3, 0),))
    init_db()
    rollup_hourly()
    check("rebuild runs once", q("SELECT temp_avg FROM hourly WHERE hour = ?", t(3, 0)) == [(99,)])
