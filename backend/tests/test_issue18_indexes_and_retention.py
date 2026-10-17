"""#18: indexes exist, statistics gathered, events/devices retention.
"""

from support import check


def test_issue18_indexes_and_retention(env):
    import base64, sqlite3
    import server
    import config, store, nodes, api
    server.prepare()   # importing does no work (#73)
    db = lambda: sqlite3.connect(config.DB)
    idx = {r[0] for r in db().execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")}
    check("5 indexes created", idx == {"ix_presence_device_id", "ix_presence_device_ts", "ix_readings_device_id", "ix_readings_device_ts", "ix_events_type_ts"})
    check("ANALYZE ran (sqlite_stat1 exists)", db().execute("SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()[0] == 1)
    store.init_db()
    check("second start is fine (IF NOT EXISTS)", len({r[0] for r in db().execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")}) == 5)
    con = db()
    con.execute("INSERT INTO events (device, ts, type, value) VALUES ('x', datetime('now','-31 days'), 'RESTART', 'old')")
    con.execute("INSERT INTO events (device, ts, type, value) VALUES ('x', datetime('now','-29 days'), 'RESTART', 'keep')")
    con.execute("INSERT INTO devices VALUES ('retired_node', datetime('now','-31 days'))")
    con.execute("INSERT INTO devices VALUES ('quiet_node', datetime('now','-29 days'))")
    con.commit(); con.close()
    store.log_event("home_ping_esp32", "OCCUPANCY_CHANGE", "occupied")
    vals = [r[0] for r in db().execute("SELECT value FROM events WHERE device='x'")]
    check("event older than 30 days deleted, 29 days kept", vals == ["keep"])
    nodes.touch_device("home_ping_esp32")
    devs = {r[0] for r in db().execute("SELECT device FROM devices")}
    check("device unseen 31 days removed, 29 days kept", "retired_node" not in devs and "quiet_node" in devs and "home_ping_esp32" in devs)
    c = api.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    check("/api/devices lists the remaining ones", {d["device"] for d in c.get("/api/devices", headers=A).get_json()} == devs)
