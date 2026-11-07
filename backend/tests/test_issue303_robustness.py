"""#303: each 5-minute step on its own (a failing one doesn't skip the fridge check), the
presence device checked like the env one, ?days=² is a 400, non-room devices left out of
the air and 24 h stats, state.reset() clears the push back-off."""
import base64
import json
import types

from support import check


def test_issue303_robustness(env):
    import server, api, ingest, server_checks, state
    from store import db, utcnow
    server.prepare()

    def q(sql, *args):
        with db() as conn:
            return conn.execute(sql, args).fetchall()

    # --- the summaries loop: fridge and baseline are their own steps, a raising one skips nothing
    names = [n for n, _ in ingest.SUMMARY_STEPS]
    check("fridge first, baseline after the rollup",
          names[0] == "fridge alert check" and names.index("baseline check") > names.index("hourly rollup"))
    ran = []
    def boom():
        raise OSError("disk gone")
    saved = ingest.SUMMARY_STEPS
    ingest.SUMMARY_STEPS = (("a", boom), ("b", lambda: ran.append("b")), ("c", boom), ("d", lambda: ran.append("d")))
    ingest.run_summary_steps()
    ingest.SUMMARY_STEPS = saved
    check("steps after a raising one still run", ran == ["b", "d"])

    # --- check_watchdogs: a failing disk check doesn't skip the certificates
    certs = []
    server_checks.disk_free_mb = boom
    server_checks.check_certs = lambda now: certs.append(now)
    server_checks.check_watchdogs()
    check("certificates checked after the disk check raised", certs == [None])

    # --- the presence device is checked like the env one
    mqtt = lambda p: ingest.on_message(None, None, types.SimpleNamespace(
        retain=False, topic="home/esp32/presence", payload=json.dumps(p).encode()))
    mqtt({"device": 42, "presence": True})
    mqtt({"device": "x" * 300, "presence": True})
    check("bad presence device IDs dropped", q("SELECT COUNT(*) FROM presence_readings") == [(0,)])
    mqtt({"presence": True, "moving": True})
    check("no device: the radar's default", q("SELECT DISTINCT device FROM presence_readings") == [(ingest.PRESENCE_DEVICE,)])

    # --- ?days= with a Unicode digit
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()
    for path in ("/api/hourly", "/api/daily"):
        r = client.get(path + "?days=²", headers=auth)
        check(f"{path}?days=² is a 400", r.status_code == 400)

    # --- a non-room device doesn't leak into /api/air or the 24 h stats
    now = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute("INSERT INTO readings (ts, device, temp, humidity, co2, pressure) VALUES (?, ?, ?, ?, ?, ?)",
                     (now, "home_ping_esp32", 21.0, 45.0, 600, 1010.0))
        conn.execute("INSERT INTO readings (ts, device, temp, humidity, co2, pressure) VALUES (?, ?, ?, ?, ?, ?)",
                     (now, "fridge_temp_esp32", 4.0, 90.0, 5000, 900.0))
    air = client.get("/api/air", headers=auth).get_json()
    check(f"/api/air is the room ({air['co2']}, {air['pressure']})", air["co2"] == 600 and air["pressure"] == 1010.0)
    stats = client.get("/api/stats24h_combined", headers=auth).get_json()
    check(f"24 h stats are the room ({stats['avg_temp']})", stats["avg_temp"] == 21.0)
    hourly = client.get("/api/hourly?days=1", headers=auth).get_json()
    check(f"/api/hourly CO2 is the room ({hourly[0]['avg_co2']})", hourly[0]["avg_co2"] == 600)

    # --- state.reset() clears the push back-off
    state.PUSH_FAILURES["x"] = 3
    state.reset()
    check("PUSH_FAILURES cleared", state.PUSH_FAILURES == {})
