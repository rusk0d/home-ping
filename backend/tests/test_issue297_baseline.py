"""#297: the baseline alert checks each new hour once, against the same hour of the day over
28 days; two unusual hours in a row push, back under |z| 2 pushes "Back to normal"."""
import time
from datetime import timedelta

from support import check


def test_issue297_baseline(env):
    import server
    import config, alerts, baseline
    from store import db, get_state, set_state, utcnow
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushes = []

    def accept(msg):
        pushes.append(msg["data"])
        return "id"
    m.send.side_effect = accept

    hour0 = utcnow().replace(minute=0, second=0, microsecond=0)
    h = lambda ago: (hour0 - timedelta(hours=ago)).strftime("%Y-%m-%d %H:%M:%S")

    def put(ago, temp, humidity=45.0, co2=600):
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO hourly (hour, temp_avg, humidity_avg, co2_avg) VALUES (?, ?, ?, ?)",
                         (h(ago), temp, humidity, co2))

    # --- too little history: nothing, however far off
    put(30, 22.0)
    put(2, 35.0)
    check("too little history: not checked", baseline.check_baseline() == {})

    # 28 days of steady history at every hour (21.5..22.5 °C)
    for day in range(1, 29):
        for hr in range(24):
            put(day * 24 + hr, 21.5 + (day % 3) * 0.5)
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key LIKE 'baseline%'")

    put(2, 30.0)
    z = baseline.check_baseline()
    check(f"unusual hour scored (z={z})", z.get("temp", 0) > baseline.BASELINE_Z)
    check("one unusual hour: no push yet", pushes == [])
    check("humidity and CO2 normal", abs(z["humidity"]) < 1 and abs(z["co2"]) < 1)
    check("same hour again: not checked twice", baseline.check_baseline() == {}
          and get_state("baseline_count:temp") == "1")

    put(1, 30.0)
    baseline.check_baseline()
    check(f"second hour in a row: pushed ({pushes})", pushes == [{
        "title": "Unusual reading", "event": "BASELINE", "device": "temp",
        "body": "Temperature is unusually high for this time of day: 30.0 °C (usually 22.0)"}])
    check("excursion saved as sent", get_state("baseline_alerted:temp") == "sent")

    put(0, 23.0)   # back within |z| 2
    baseline.check_baseline(time.time() + config.NOTIFICATION_COOLDOWN + 1)
    check("back to normal pushed", len(pushes) == 2 and pushes[1]["body"].startswith("Temperature is back to normal")
          and get_state("baseline_alerted:temp") == "")

    # --- a failed push is retried on the next hour
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key LIKE 'baseline%'")
        conn.execute("DELETE FROM hourly WHERE hour >= ?", (h(2),))
    alerts.state.LAST_NOTIFICATION.clear()
    put(2, 30.0); baseline.check_baseline()
    m.send.side_effect = Exception("FCM down")
    put(1, 30.0); baseline.check_baseline()
    check("failed push: excursion not saved", get_state("baseline_alerted:temp", "") in ("", None))
    m.send.side_effect = accept
    put(0, 30.0); baseline.check_baseline(time.time() + config.NOTIFICATION_COOLDOWN + 1)
    check("retried on the next hour", len(pushes) == 3 and get_state("baseline_alerted:temp") == "sent")

    # --- a muted excursion ends without "Back to normal"
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key LIKE 'baseline%'")
        conn.execute("DELETE FROM hourly WHERE hour >= ?", (h(2),))
    alerts.state.LAST_NOTIFICATION.clear()
    set_state("notify:baseline", "0")
    put(2, 30.0); baseline.check_baseline()
    put(1, 30.0); baseline.check_baseline()
    check("switched off: muted", get_state("baseline_alerted:temp") == "muted" and len(pushes) == 3)
    set_state("notify:baseline", "1")
    put(0, 22.0); baseline.check_baseline(time.time() + config.NOTIFICATION_COOLDOWN + 1)
    check("muted excursion ends quietly", len(pushes) == 3 and get_state("baseline_alerted:temp") == "")

    # --- an old newest hour (node down) isn't checked
    with db() as conn:
        conn.execute("DELETE FROM hourly WHERE hour >= ?", (h(5),))
        conn.execute("DELETE FROM system_state WHERE key = 'baseline_last_hour'")
    put(5, 40.0)
    check("stale hour skipped", baseline.check_baseline() == {})
