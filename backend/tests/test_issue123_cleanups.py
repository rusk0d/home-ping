"""#123: request size limit, SQL averages in stats24h_combined, the parse_ts / is_online helpers."""
import base64
from datetime import datetime, timedelta

from support import check


def test_issue123_cleanups(env):
    import server, api
    from store import db, parse_ts, utcnow
    server.prepare()
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    # Request size limit: JSON 413 instead of reading the body
    r = c.post("/api/notification_settings", data=b"x" * (16 * 1024 + 1),
               headers={**auth, "Content-Type": "application/json"})
    check("too large -> 413", r.status_code == 413)
    check("413 is JSON", r.is_json and "error" in r.get_json())
    r = c.post("/api/occupancy_arm", json={"armed": True}, headers=auth)
    check("normal body still fine", r.status_code == 200)

    # stats24h_combined: no rows -> null, then the SQL averages (rows with both values only)
    s = c.get("/api/stats24h_combined", headers=auth).get_json()
    check("no rows -> null averages", s["avg_temp"] is None and s["avg_humidity"] is None)
    now = utcnow()
    ts = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.executemany("INSERT INTO readings (device, ts, temp, humidity) VALUES ('home_ping_esp32', ?, ?, ?)", [
            (ts(1), 21.0, 40.0), (ts(2), 22.0, 45.0), (ts(3), 23.05, 50.0),
            (ts(4), 30.0, None),     # no humidity: not averaged
            (ts(25), 10.0, 10.0),    # older than 24 h
        ])
    s = c.get("/api/stats24h_combined", headers=auth).get_json()
    check("SQL averages, rounded", s["avg_temp"] == 22.0 and s["avg_humidity"] == 45.0)

    # Helpers
    check("parse_ts", parse_ts("2026-10-16 08:09:10") == datetime(2026, 10, 16, 8, 9, 10))
    fresh = now.strftime("%Y-%m-%d %H:%M:%S")
    stale = (now - timedelta(seconds=api.HEARTBEAT_TIMEOUT + 5)).strftime("%Y-%m-%d %H:%M:%S")
    check("is_online", api.is_online(fresh) and not api.is_online(stale) and not api.is_online(None))
