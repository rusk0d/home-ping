"""#15: register_token must not add a device; init_db removes an old "unknown" row.
"""

from support import BACKEND, check


def test_issue15_register_token(env):
    backend = str(BACKEND)
    import os, sys, base64, tempfile, sqlite3
    from unittest import mock
    import server
    import config, store, alerts, api
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
    r = c.post("/api/register_token", json={"token": "tok123"}, headers=auth)
    check("register -> 200 success", r.status_code == 200 and r.get_json() == {"success": True})
    r = c.post("/api/register_token", json={"token": "tok456", "device": "phone"}, headers=auth)
    check("register with a device -> 200", r.status_code == 200)
    check("register without token -> 400", c.post("/api/register_token", json={}, headers=auth).status_code == 400)
    check("register adds no device", devices() == ["home_ping_esp32"])
    calls = [cl.args for cl in alerts.messaging.subscribe_to_topic.call_args_list]
    check("tokens subscribed to the env topic", calls == [(["tok123"], "env"), (["tok456"], "env")])
