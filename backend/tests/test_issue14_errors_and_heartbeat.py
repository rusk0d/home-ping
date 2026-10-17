"""#14 part 2: JSON 400s/errors, co2_ok in the DB, one heartbeat (120 s).
"""

from support import check


def test_issue14_errors_and_heartbeat(env):
    import json, base64, sqlite3, types
    from datetime import timedelta
    from unittest import mock
    import server
    import config, store, ingest, api
    server.prepare()   # importing does no work (#73)
    c = api.app.test_client()
    A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    def is_json_error(r, code): return r.status_code == code and r.is_json and "error" in r.get_json()

    # 400s instead of 500s
    c.post("/api/occupancy_arm", json={"armed": True}, headers=A)
    check("occupancy_arm, no body -> JSON 400", is_json_error(c.post("/api/occupancy_arm", headers=A), 400))
    check("occupancy_arm, armed='yes' -> JSON 400", is_json_error(c.post("/api/occupancy_arm", json={"armed": "yes"}, headers=A), 400))
    check("...and the bad requests did not disarm", c.get("/api/occupancy_status", headers=A).get_json() == {"armed": True})
    check("occupancy_arm false -> 200", c.post("/api/occupancy_arm", json={"armed": False}, headers=A).get_json() == {"armed": False})
    check("unknown path -> JSON 404", is_json_error(c.get("/api/nope", headers=A), 404))
    with mock.patch.object(store.sqlite3, "connect", side_effect=RuntimeError("db gone")):
        r = c.get("/api/devices", headers=A)
    check("unexpected error -> JSON 500 without details", is_json_error(r, 500) and "db gone" not in r.get_data(as_text=True))
    check("auth failure still 401", c.get("/api/devices").status_code == 401)

    # co2_ok persisted
    def mqtt(payload):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/co2", payload=json.dumps(payload).encode()))
    mqtt({"device": "co2_sensor_esp32", "co2": 900, "co2_ok": False, "uptime": 5})
    check("co2_ok false stored in readings", sqlite3.connect(config.DB).execute("SELECT co2_ok FROM readings ORDER BY id DESC LIMIT 1").fetchone()[0] == 0)
    check("latest_all co2_ok False (read from DB)", c.get("/api/latest_all", headers=A).get_json()["co2_sensor_esp32"]["co2_ok"] is False)
    mqtt({"device": "co2_sensor_esp32", "co2": 905, "co2_ok": True, "uptime": 65})
    check("latest_all co2_ok True", c.get("/api/latest_all", headers=A).get_json()["co2_sensor_esp32"]["co2_ok"] is True)
    check("no in-memory CO2_SENSOR_OK any more", not hasattr(server, "CO2_SENSOR_OK"))
    # old DB without the column gets it on start
    con = sqlite3.connect(config.DB); cols = [r[1] for r in con.execute("PRAGMA table_info(readings)")]; con.close()
    check("readings has co2_ok column", "co2_ok" in cols)

    # heartbeat 120 s
    check("HEARTBEAT_TIMEOUT == 120", config.HEARTBEAT_TIMEOUT == 120)
    con = sqlite3.connect(config.DB)
    for dev, age in (("fresh", 60), ("stale", 150)):
        con.execute("INSERT OR REPLACE INTO devices VALUES (?, ?)", (dev, (store.utcnow() - timedelta(seconds=age)).strftime("%Y-%m-%d %H:%M:%S")))
    con.commit(); con.close()
    online = {d["device"]: d["online"] for d in c.get("/api/devices", headers=A).get_json()}
    check("60 s ago online, 150 s ago offline", online["fresh"] is True and online["stale"] is False)
