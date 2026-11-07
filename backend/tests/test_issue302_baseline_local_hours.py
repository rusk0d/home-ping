"""#302: the baseline compares the same local hour (a DST change doesn't shift the household's
rhythm), and "2 hours in a row" means two consecutive hours."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from support import check

BUDAPEST = ZoneInfo("Europe/Budapest")   # the tests run in this zone (conftest), like the server


def test_issue302_baseline_local_hours(env):
    import server
    import alerts, baseline
    from store import db, get_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushes = []
    m.send.side_effect = lambda msg: pushes.append(msg["data"]) or "id"

    fixed_now = datetime(2026, 11, 7, 6, 30)   # UTC; CET since the 2026-10-25 switch
    baseline.utcnow = lambda: fixed_now
    fmt = lambda dt: dt.strftime("%Y-%m-%d %H:00:00")
    local_hour = lambda dt: dt.replace(tzinfo=timezone.utc).astimezone(BUDAPEST).hour

    def put(dt, temp):
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO hourly (hour, temp_avg) VALUES (?, ?)", (fmt(dt), temp))

    def reset():
        with db() as conn:
            conn.execute("DELETE FROM hourly")
            conn.execute("DELETE FROM system_state WHERE key LIKE 'baseline%'")

    # --- 28 days with a morning routine at 06:00 local: 30 °C then, 20 °C otherwise.
    # Before the switch 06:00 local was 04:00 UTC, after it 05:00 UTC.
    newest = datetime(2026, 11, 7, 5)          # 06:00 local
    t = newest - timedelta(days=28)
    while t < newest:
        put(t, 30.0 if local_hour(t) == 6 else 20.0)
        t += timedelta(hours=1)
    put(newest, 30.0)
    z = baseline.check_baseline()
    check(f"same local hour across the DST change: normal (z={z})", abs(z["temp"]) < 1)

    # --- two unusual hours with a gap between them aren't "in a row"
    reset()
    steady = datetime(2026, 11, 7, 3)
    for day in range(1, 29):
        for hr in range(24):
            put(steady - timedelta(days=day, hours=hr), 21.0 + (day % 3) * 0.5)
    put(steady, 30.0)                          # 03:00 UTC unusual
    baseline.check_baseline()
    put(steady + timedelta(hours=2), 30.0)     # 04:00 missing (node down), 05:00 unusual
    baseline.check_baseline()
    check("gap: counted from 1 again, no push", pushes == [] and get_state("baseline_count:temp") == "1")
    put(steady + timedelta(hours=3), 30.0)     # 06:00 unusual, right after 05:00
    fixed_now = datetime(2026, 11, 7, 7, 30)
    baseline.check_baseline()
    check("then two in a row: pushed", len(pushes) == 1 and pushes[0]["event"] == "BASELINE")
    with db() as conn:
        detail = conn.execute("SELECT value FROM events WHERE type = 'BASELINE_DEVIATION' "
                              "ORDER BY id DESC LIMIT 1").fetchone()[0]
    check(f"detail in local time ({detail})", "at 07:00," in detail)   # 06:00 UTC
