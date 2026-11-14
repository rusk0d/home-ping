"""#427: the weekly summary push on Sunday evening: night CO₂ against the week before, times
aired, fridge door pushes, restarts and server warnings; once per week, switch "weekly"."""

import base64
from datetime import datetime, timedelta, timezone

from support import check, quiet_now


def test_issue427_weekly_summary(env):
    import server
    import alerts, api, store, weekly
    from store import db, get_state, set_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushed = []
    m.send.side_effect = lambda msg: pushed.append((msg["data"]["title"], msg["data"]["body"], msg["data"]["event"])) or "id"
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()

    # --- hourly: night CO₂ 900 this week, 1000 the week before; days 2000 (not night)
    now = store.utcnow()
    week_ago = now - timedelta(days=7)
    top = now.replace(minute=0, second=0, microsecond=0)
    rows = []
    for h in range(1, 14 * 24):
        hour = top - timedelta(hours=h)
        if abs(hour - week_ago) <= timedelta(hours=1):
            continue   # away from the edge of the window
        local = hour.replace(tzinfo=timezone.utc).astimezone().hour
        night = local >= 22 or local < 7
        co2 = (900 if hour > week_ago else 1000) if night else 2000
        rows.append((hour.strftime("%Y-%m-%d %H:%M:%S"), co2))

    def event(device, type_, value="sent", days=1):
        with db() as conn:
            conn.execute("INSERT INTO events (device, ts, type, value) VALUES (?, datetime('now', ?), ?, ?)",
                         (device, f"-{days} days", type_, value))

    with db() as conn:
        conn.executemany("INSERT INTO hourly (hour, co2_avg) VALUES (?, ?)", rows)
    for _ in range(3):
        event("home_ping_esp32", "WINDOW_OPEN", "CO₂ -94 ppm")
    event("home_ping_esp32", "WINDOW_OPEN", days=8)                 # last week's
    event("fridge_temp_esp32", "FRIDGE_DOOR_OPEN")
    event("fridge_temp_esp32", "FRIDGE_DOOR_OPEN", "muted (off), temp=7.8", days=3)
    event("fridge_temp_esp32", "RESTART", days=2)
    event("fridge_temp_esp32", "RESTART", days=3)
    event("home_ping_esp32", "RESTART")
    event("backup", "WATCHDOG_BACKUP")
    event("backup", "WATCHDOG_BACKUP_OK")
    event("disk", "WATCHDOG_DISK", days=10)                           # before this week

    s = weekly.summary()
    check(f"summary ({s})", s == {"night_co2": 900, "night_co2_before": 1000, "aired": 3, "fridge_door": 2,
                                   "restarts": {"fridge_temp_esp32": 2, "home_ping_esp32": 1},
                                   "warnings": ["backup"]})
    body = weekly.text(s)
    check(f"text ({body})", body == "Night CO₂ 900 ppm (−100 on last week) · aired 3× · fridge door 2× · "
                                    "3 restarts (Fridge 2, Main node 1) · server warning: backup")
    quiet = {"night_co2": None, "night_co2_before": None, "aired": 0, "fridge_door": 0, "restarts": {}, "warnings": []}
    check(f"a quiet week ({weekly.text(quiet)})",
          weekly.text(quiet) == "Not aired · fridge door fine · no restarts · server all fine")
    check("no week before: no comparison",
          weekly.text({**quiet, "night_co2": 850}).startswith("Night CO₂ 850 ppm · "))
    check("two warnings", weekly.text({**quiet, "warnings": ["backup", "disk"]}).endswith("server warnings: backup, disk"))
    check("the same as last week",
          weekly.text({**quiet, "night_co2": 850, "night_co2_before": 850}).startswith("Night CO₂ 850 ppm (same as last week) · "))

    # --- when: Sunday from 19:00 local, once per ISO week
    sunday = datetime(2026, 11, 15, 19, 5)
    check("Saturday: no", weekly.check_weekly(sunday - timedelta(days=1)) is None)
    check("Sunday 18:59: no", weekly.check_weekly(sunday.replace(hour=18, minute=59)) is None and pushed == [])
    check("Sunday 19:05: sent", weekly.check_weekly(sunday) == "sent")
    check(f"pushed once, event WEEKLY ({pushed})", pushed == [("Your week at home", body, "WEEKLY")])
    check("again at 19:10: no", weekly.check_weekly(sunday + timedelta(minutes=5)) is None and len(pushed) == 1)
    check("Monday: no", weekly.check_weekly(sunday + timedelta(hours=6)) is None and len(pushed) == 1)
    check("next Sunday: sent", weekly.check_weekly(sunday + timedelta(days=7)) == "sent" and len(pushed) == 2)
    with db() as conn:
        logged = [r[0] for r in conn.execute("SELECT value FROM events WHERE type = 'WEEKLY_SUMMARY'")]
    check(f"logged ({logged})", logged == [f"sent, {body}"] * 2)

    # --- quiet hours postpone it; a failed send is tried again
    pushed.clear()
    set_state("weekly_sent", "")
    quiet_now()
    check("quiet hours: held", weekly.check_weekly(sunday) is None and pushed == [] and get_state("weekly_sent") == "")
    quiet_now(on=False)
    m.send.side_effect = RuntimeError("FCM down")
    check("failed: not marked", weekly.check_weekly(sunday) is False and get_state("weekly_sent") == "")
    m.send.side_effect = lambda msg: pushed.append(msg["data"]["title"]) or "id"
    check("then sent", weekly.check_weekly(sunday + timedelta(minutes=5)) == "sent" and pushed == ["Your week at home"])

    # --- the switch
    check("on by default", client.get("/api/notification_settings", headers=auth).get_json()["weekly"] is True)
    r = client.post("/api/notification_settings", json={"weekly": False}, headers=auth)
    check("switch in the settings", r.status_code == 200 and r.get_json()["weekly"] is False)
    pushed.clear()
    set_state("weekly_sent", "")
    check("switched off: muted, no push", weekly.check_weekly(sunday) == "muted" and pushed == []
          and get_state("weekly_sent") == "2026-W46")
