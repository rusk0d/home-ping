"""#245 step 2: the air pushes. "Time to air out" while CO₂ stays high and someone is home
(postponed by quiet hours and while nobody is home, again every 3 h, "Air is fresh again"
after it), "Close the window?" once per spell and "Window still open?" when the geofence arms.
"""

import base64
import time
from datetime import datetime, timedelta

from support import check


def test_issue245_air_pushes(env):
    import sqlite3
    import server
    import config, store, alerts, air, api
    server.prepare()

    pushes = []
    ok = [True]
    alerts.send_push = lambda title, body, data=None: pushes.append((title, body, data)) or ok[0]
    conn = sqlite3.connect(config.DB)

    def feed(start, end, co2=None, temp=None):
        """A reading every 30 s from [start] to [end] (CO₂ and temperature rows, like the node)."""
        ts = start
        while ts <= end:
            stamp = ts.strftime("%Y-%m-%d %H:%M:%S")
            if co2 is not None:
                conn.execute("INSERT INTO readings (device, ts, co2, co2_ok) VALUES (?, ?, ?, 1)",
                             (config.AIR_DEVICE, stamp, co2))
            if temp is not None:
                conn.execute("INSERT INTO readings (device, ts, temp, humidity) VALUES (?, ?, ?, 50)",
                             (config.AIR_DEVICE, stamp, temp))
            ts += timedelta(seconds=30)
        conn.commit()

    def at(now, co2=None, temp=None):
        """The last 10 min of readings up to [now], then one check at [now]."""
        conn.execute("DELETE FROM readings"); conn.commit()
        feed(now - timedelta(minutes=10), now, co2, temp)
        air.check_air(now)

    def events(kind):
        return [v for (v,) in conn.execute("SELECT value FROM events WHERE type = ? ORDER BY id", (kind,))]

    def home(yes):
        conn.execute("DELETE FROM presence_readings")
        if yes:
            conn.execute("INSERT INTO presence_readings (device, ts, occupied) VALUES (?, datetime('now'), 1)",
                         (config.PRESENCE_DEVICE,))
        conn.commit()

    def quiet(on):
        local = datetime.now()
        store.set_state("quiet_hours", "1" if on else "0")
        store.set_state("quiet_start", (local - timedelta(hours=1)).strftime("%H:%M"))
        store.set_state("quiet_end", (local + timedelta(hours=1)).strftime("%H:%M"))

    t = store.utcnow().replace(microsecond=0) - timedelta(hours=10)
    home(True)

    # Quiet hours postpone the push until they end
    quiet(True)
    at(t, co2=1300)
    check("quiet hours: no push, logged why", pushes == []
          and events("AIR_HIGH") == ["no push yet (quiet hours), CO₂ 1300 ppm for 10 min, no weather"])
    at(t + timedelta(minutes=1), co2=1300)
    check("...still nothing while they last, and logged once", pushes == [] and len(events("AIR_HIGH")) == 1)
    quiet(False)
    at(t + timedelta(minutes=2), co2=1300)
    check("after quiet hours: Time to air out",
          pushes == [("Time to air out", "CO₂ 1300 ppm: open a window for 5-10 minutes.",
                      {"event": "AIR", "device": "co2"})]
          and events("AIR_HIGH")[-1].startswith("sent, CO₂ 1300 ppm"))
    at(t + timedelta(minutes=3), co2=1300)
    check("...once", len(pushes) == 1)

    # Reminder every AIR_REMINDER_HOURS while it stays high
    at(t + timedelta(hours=2, minutes=59), co2=1300)
    check("not before 3 h", len(pushes) == 1)
    at(t + timedelta(hours=3, minutes=2), co2=1350)
    check("after 3 h: reminder", len(pushes) == 2 and pushes[-1][1].startswith("CO₂ still 1350 ppm after 3 h"))
    at(t + timedelta(hours=4), co2=1100)
    check("between 900 and 1200: same excursion, no push", len(pushes) == 2 and events("AIR_OK") == [])

    # The switch off: muted, logged once per reminder period
    store.set_state("notify:air", "0")
    at(t + timedelta(hours=6, minutes=3), co2=1300)
    at(t + timedelta(hours=6, minutes=4), co2=1300)
    check("switched off: no push, muted once", len(pushes) == 2
          and sum(e.startswith("muted (off)") for e in events("AIR_HIGH")) == 1)
    store.set_state("notify:air", "1")

    # Fresh again: replaces the push on the phone, and the excursion is over
    at(t + timedelta(hours=6, minutes=30), co2=850)
    check("below 900: Air is fresh again", pushes[-1] == ("Air is fresh again", "CO₂ is down to 850 ppm.",
                                                          {"event": "AIR", "device": "co2"})
          and store.get_state("air:co2_high", "x") == "" and store.get_state("air:co2_push", "x") == "")

    # A failed send is retried after AIR_RETRY_MINUTES, not every minute
    t2 = t + timedelta(hours=7)
    ok[0] = False
    at(t2, co2=1300)
    at(t2 + timedelta(minutes=1), co2=1300)
    check("failed: logged once", len(pushes) == 4 and events("AIR_HIGH")[-1].startswith("failed, "))
    ok[0] = True
    at(t2 + timedelta(minutes=config.AIR_RETRY_MINUTES + 1), co2=1300)
    check("...retried after 15 min", len(pushes) == 5 and events("AIR_HIGH")[-1].startswith("sent, "))
    at(t2 + timedelta(minutes=30), co2=800)
    check("...and replaced when fresh", pushes[-1][0] == "Air is fresh again")

    # Nobody home: no push, until someone is back and it's still high
    home(False)
    t3 = t + timedelta(hours=8)
    at(t3, co2=1300)
    check("nobody home: no push", len(pushes) == 6 and events("AIR_HIGH")[-1].startswith("no push yet (nobody home)"))
    home(True)
    at(t3 + timedelta(hours=1, minutes=10), co2=1250)
    check("home again, still high: pushed", len(pushes) == 7 and pushes[-1][1].startswith("CO₂ still 1250 ppm after 1 h"))
    at(t3 + timedelta(hours=1, minutes=20), co2=800)
    pushes.clear()

    # Weather changes the advice, not whether it pushes
    air.WEATHER = {"at": time.time(), "temp": 12.0, "humidity": 80, "precipitation": 0.6}
    at(t3 + timedelta(hours=1, minutes=40), co2=1300)
    check("raining: tilt the window", pushes[-1][1] == "CO₂ 1300 ppm: it's raining, tilt a window for a while.")
    at(t3 + timedelta(hours=1, minutes=50), co2=800)

    # "Close the window?": open 20+ min, the air fresh, much colder outside
    pushes.clear()
    t4 = t + timedelta(hours=9)
    since = (t4 - timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M:%S")
    store.set_state("air:window", f"{since}|23.0")
    air.WEATHER = {"at": time.time(), "temp": 18.0, "humidity": 60, "precipitation": 0}
    at(t4, co2=500, temp=21.0)
    check("mild outside: no nudge", pushes == [] and store.get_state("air:window") != "")
    air.WEATHER["temp"] = 6.0
    quiet(True)
    at(t4 + timedelta(minutes=1), co2=500, temp=21.0)
    check("quiet hours: postponed", pushes == [])
    quiet(False)
    at(t4 + timedelta(minutes=2), co2=500, temp=21.0)
    check("cold outside, CO₂ low, 25+ min: Close the window?", len(pushes) == 1
          and pushes[0][0] == "Close the window?" and pushes[0][2] == {"event": "AIR", "device": "window"}
          and events("WINDOW_CLOSE")[-1].startswith("sent, open 27 min"))
    at(t4 + timedelta(minutes=3), co2=500, temp=21.0)
    check("...once per spell", len(pushes) == 1)

    # "Window still open?" when the phone's geofence arms occupancy during a spell
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    store.set_state("air:window", f"{(store.utcnow() - timedelta(minutes=12)):%Y-%m-%d %H:%M:%S}|23.0")
    c.post("/api/occupancy_arm", json={"armed": True, "source": "manual"}, headers=auth)
    c.post("/api/occupancy_arm", json={"armed": False, "source": "location"}, headers=auth)
    check("manual arm or disarm: no window push", len(pushes) == 1)
    r = c.post("/api/occupancy_arm", json={"armed": True, "source": "location"}, headers=auth)
    check("leaving with a window open: Window still open?", r.status_code == 200 and len(pushes) == 2
          and pushes[-1] == ("Window still open?", "You left home and a window has been open for 12 min.",
                             {"event": "AIR", "device": "window"}))
    store.set_state("air:window", "")
    c.post("/api/occupancy_arm", json={"armed": True, "source": "location"}, headers=auth)
    check("...no spell, no push", len(pushes) == 2)

    # The switch in the notification settings
    r = c.get("/api/notification_settings", headers=auth)
    check("air switch in the settings, on by default", r.get_json()["air"] is True)
    air.WEATHER = None
