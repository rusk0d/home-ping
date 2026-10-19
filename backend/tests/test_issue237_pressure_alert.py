"""#237: push when the pressure falls fast (3 h change of the hourly averages).
"""

from support import check


def test_issue237_pressure_alert(env):
    import sqlite3
    from datetime import datetime, timedelta
    import server
    import config, state, store, alerts, ingest
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [c.kwargs["notification"].body if c.kwargs.get("notification") else c.kwargs["data"]
               for c in m.Message.call_args_list if c.kwargs.get("data", {}).get("event") == "PRESSURE"]
        m.Message.reset_mock()
        return out

    base = datetime(2026, 10, 19, 6)   # UTC

    def hours(*values):
        """hourly rows from base on, one per value (None = no pressure that hour)."""
        conn = sqlite3.connect(config.DB)
        conn.execute("DELETE FROM hourly")
        for i, v in enumerate(values):
            conn.execute("INSERT INTO hourly (hour, pressure_avg) VALUES (?, ?)",
                         ((base + timedelta(hours=i)).strftime("%Y-%m-%d %H:00:00"), v))
        conn.commit()
        # "now" is just after the last hour completed
        return base + timedelta(hours=len(values), minutes=5)

    def run(*values):
        state.LAST_NOTIFICATION.pop("PRESSURE", None)   # cooldown tested separately
        ingest.check_pressure(hours(*values))
        return pushes()

    check("no data: nothing", run() == [] and ingest.pressure_change(base) is None)
    check("fewer than 3 h of data: nothing", run(1015, 1014, 1013) == [])
    check("steady / slow fall: nothing", run(1015, 1014.5, 1014, 1013.5, 1013) == [])

    p = run(1015, 1015, 1014, 1013, 1011.5)   # newest 1011.5 vs 3 h before 1015 = -3.5
    check("-3.5 hPa in 3 h: one push", len(p) == 1)
    check("change from the newest hour vs 3 h before",
          abs(ingest.pressure_change(base + timedelta(hours=5)) - -3.5) < 1e-9)
    check("once per excursion", run(1015, 1015, 1014, 1013, 1011.5, 1010) == [])
    check("-2.5 (inside the hysteresis): still the same excursion",
          run(1015, 1014, 1013, 1012.5, 1011.5) == [] and store.get_state("env_alerted:PRESSURE") == "falling")
    check("back above -2.0: excursion over", run(1012, 1012, 1012, 1012, 1011) == []
          and store.get_state("env_alerted:PRESSURE", "") == "")
    check("a second fall alerts again", len(run(1015, 1015, 1014, 1013, 1011)) == 1)

    # Gaps and stale data
    store.set_state("env_alerted:PRESSURE", "")
    check("the hour 3 h before has no pressure: nothing", run(1015, None, 1014, 1013, 1011) == [])
    ingest.check_pressure(hours(1015, 1015, 1014, 1013, 1011) + timedelta(hours=3))
    check("newest hour is old (node down): nothing", pushes() == [])

    # Muted by its own switch and by quiet hours (treated as sent: no push later in the excursion)
    store.set_state("notify:pressure", "0")
    check("switch off: no push", run(1015, 1015, 1014, 1013, 1011) == [] and store.get_state("env_alerted:PRESSURE") == "falling")
    store.set_state("notify:pressure", "1")
    check("...and none once it's back on, same excursion", run(1015, 1015, 1014, 1013, 1011) == [])
    store.set_state("env_alerted:PRESSURE", "")
    store.set_state("quiet_hours", "1"); store.set_state("quiet_start", "00:00"); store.set_state("quiet_end", "23:59")
    check("quiet hours: muted", run(1015, 1015, 1014, 1013, 1011) == [])
    store.set_state("quiet_hours", "0")

    # Cooldown: a new excursion right after the last push waits
    store.set_state("env_alerted:PRESSURE", "")
    run(1015, 1015, 1014, 1013, 1011)
    store.set_state("env_alerted:PRESSURE", "")
    ingest.check_pressure(hours(1015, 1015, 1014, 1013, 1011))
    check("cooldown after a push", pushes() == [])

    check("the switch is a notification kind", "pressure" in config.NOTIFY_KINDS)
