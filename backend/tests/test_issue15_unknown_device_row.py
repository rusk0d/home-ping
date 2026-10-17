"""#15: init_db removes the old "unknown" row the phone used to get. (/api/register_token
itself was removed in #172.)
"""

from support import check


def test_issue15_unknown_device_row(env):
    import base64, sqlite3
    import server
    import config, store, api
    server.prepare()   # importing does no work (#73)
    db = config.DB
    # Simulate an existing DB with the old phone row, then run init_db again (= backend restart)
    con = sqlite3.connect(db)
    con.execute("INSERT OR REPLACE INTO devices VALUES ('unknown', '2026-10-15 10:00:00')")
    con.execute("INSERT OR REPLACE INTO devices VALUES ('home_ping_esp32', '2026-10-15 10:00:00')")
    con.commit(); con.close()
    store.init_db()
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    devices = lambda: [d["device"] for d in c.get("/api/devices", headers=auth).get_json()]
    check("restart removes the old 'unknown' device", devices() == ["home_ping_esp32"])
