"""#9: soil feature removed. Old DB keeps working, soil table dropped, endpoints gone.
"""

from support import check


def test_issue09_soil_removed(env):
    import os, sys, json, base64, tempfile, sqlite3, types
    from unittest import mock
    import server
    import config, store, alerts, ingest, api
    server.prepare()   # importing does no work (#73)
    def tables():
        con = sqlite3.connect(config.DB); t = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}; con.close(); return t
    # Simulate an old DB that still has soil_readings, then restart
    con = sqlite3.connect(config.DB)
    con.execute("CREATE TABLE IF NOT EXISTS soil_readings (id INTEGER PRIMARY KEY, device TEXT, ts TIMESTAMP, moisture INTEGER, raw_value INTEGER)")
    con.execute("INSERT INTO soil_readings (device, moisture, raw_value) VALUES ('soil_sensor_01', 40, 2000)")
    con.commit(); con.close()
    store.init_db()
    check("soil_readings dropped on start", "soil_readings" not in tables())
    check("other tables still there", {"readings", "presence_readings", "devices", "events", "system_state"} <= tables())
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    for path in ["/api/soil/latest", "/api/soil/history", "/api/soil/stats24h"]:
        check(f"{path} -> 404", c.get(path, headers=auth).status_code == 404)
    check("/api/latest_all still 200", c.get("/api/latest_all", headers=auth).status_code == 200)
    errors = []
    import logging
    class _Errors(logging.Handler):   # MQTT handler errors are logged since #66
        def emit(self, r): errors.append(r.getMessage())
    logging.getLogger("ingest").addHandler(_Errors(level=logging.ERROR))
    ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/soil", payload=json.dumps({"device": "soil_sensor_01", "moisture": 10, "raw": 3000}).encode()))
    check("stray soil message: no handler error", not errors)
    check("no soil push sent", not any("SOIL" in str(cl) for cl in alerts.messaging.send.call_args_list))
