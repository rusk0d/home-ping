"""#129: the hourly table (rollup from the raw tables) and /api/daily."""
import base64
from datetime import timedelta, timezone

from support import check


def test_issue129_hourly_table(env):
    import server, api
    from store import db, rollup_hourly, utcnow
    server.prepare()
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    hour = utcnow().replace(minute=0, second=0, microsecond=0)   # current hour (incomplete)
    def ts(h_ago, minute):
        return (hour - timedelta(hours=h_ago) + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M:%S")
    def key(h_ago):
        return (hour - timedelta(hours=h_ago)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.executemany("INSERT INTO readings (device, ts, temp, humidity, pressure, co2) VALUES ('home_ping_esp32', ?, ?, ?, ?, ?)", [
            (ts(30, 10), 20.0, 40.0, 1010.0, None),
            (ts(30, 40), 22.0, 50.0, 1012.0, None),
            (ts(30, 20), None, None, None, 600),
            (ts(30, 50), None, None, None, 900),
            (ts(2, 5), 23.0, 45.0, 1011.0, 700),
            (ts(0, 1), 25.0, 45.0, 1011.0, 800),     # current hour: not rolled up yet
        ])
        conn.executemany("INSERT INTO presence_readings (device, ts, occupied) VALUES (?, ?, ?)", [
            ("home_ping_esp32", ts(30, 0), 1), ("home_ping_esp32", ts(30, 30), 0),
            ("home_ping_esp32", ts(5, 0), 1),                 # an hour with radar data only
            ("hallway_presence_esp32", ts(2, 0), 1),          # other radars don't count
        ])
        conn.execute("DELETE FROM hourly")   # prepare() already rolled up (nothing then)
        conn.execute("DELETE FROM system_state WHERE key = 'hourly_until'")

    check("first run backfills", rollup_hourly() == 3)
    with db() as conn:
        rows = {r[0]: r for r in conn.execute("SELECT * FROM hourly")}
    check("complete hours only", set(rows) == {key(30), key(5), key(2)})
    check("avg/min/max", rows[key(30)][1:13] == (21.0, 20.0, 22.0, 45.0, 40.0, 50.0, 750.0, 600, 900, 1011.0, 1010.0, 1012.0))
    check("occupancy % of the main radar", rows[key(30)][13] == 50.0 and rows[key(5)][13] == 100.0 and rows[key(2)][13] is None)
    check("radar-only hour", rows[key(5)][1] is None)

    check("nothing new: no work", rollup_hourly() == 0)
    # An hour later: the hour before the last run's stop is redone (no data), the then-complete hour added
    check("next hour", rollup_hourly(now=utcnow() + timedelta(hours=1)) == 1)
    with db() as conn:
        check("current hour now stored", conn.execute("SELECT temp_avg FROM hourly WHERE hour = ?", (key(0),)).fetchone() == (25.0,))
        conn.execute("DELETE FROM hourly WHERE hour = ?", (key(0),))   # keep the day sums below simple

    # /api/daily: grouped by local date, newest first
    local_date = lambda h_ago: (hour - timedelta(hours=h_ago)).replace(tzinfo=timezone.utc).astimezone().date().isoformat()
    d = c.get("/api/daily?days=3", headers=auth).get_json()
    by_date = {x["date"]: x for x in d}
    check("newest first", [x["date"] for x in d] == sorted(by_date, reverse=True))
    day30 = by_date[local_date(30)]
    if local_date(30) not in {local_date(5), local_date(2)}:
        check("day values", day30["avg_temp"] == 21.0 and day30["min_temp"] == 20.0 and day30["max_temp"] == 22.0
              and day30["max_co2"] == 900 and day30["avg_co2"] == 750 and day30["occupied_hours"] == 0.5 and day30["hours"] == 1)
    check("total hours", sum(x["hours"] for x in d) == 3)
    check("occupied hours summed", round(sum(x["occupied_hours"] or 0 for x in d), 2) == 1.5)
    check("days=1 is today only", all(x["date"] == local_date(0) for x in c.get("/api/daily?days=1", headers=auth).get_json()))
    for bad in ("0", "401", "x"):
        check(f"days={bad} -> 400", c.get(f"/api/daily?days={bad}", headers=auth).status_code == 400)
    check("needs the login", c.get("/api/daily").status_code == 401)
