"""#411: the door counts as closed once the fridge cools FRIDGE_DOOR_FALL_C below its peak since
the rise, not only once it's back near where it started: warm food keeps a shut fridge warm for
hours. The readings follow the real 2026-11-08 17:22 UTC loading."""

from datetime import datetime, timedelta

from support import check


def test_issue411_door_closed_when_cooling(env):
    import server
    import alerts, fridge, store
    from config import FRIDGE_DEVICE
    from store import db
    server.prepare()

    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append(title) or True
    t = [store.utcnow() - timedelta(hours=1)]

    def reading(temp):
        t[0] += timedelta(minutes=1)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, uptime) VALUES (?, ?, ?, 3600)",
                         (FRIDGE_DEVICE, t[0].strftime("%Y-%m-%d %H:%M:%S"), temp))
        fridge.check_door(now=t[0])

    # The rule on its own: the peak follows the reading, a 0.5 °C fall from it closes
    T0 = datetime(2026, 11, 8, 17, 22)
    T = lambda mins: T0 + timedelta(minutes=mins)
    rise = (T(0), 1.3, 7.9)
    check("at the peak: still open", fridge.door_step(rise, [(T(20), 7.9)]) == (rise, "open"))
    check("0.2 below the peak: still open", fridge.door_step(rise, [(T(21), 7.7)]) == (rise, "open"))
    check("0.5 below the peak: closed", fridge.door_step(rise, [(T(22), 7.4)]) == (None, "closed"))

    # Through check_door: food loaded, 15 min at the peak, then cooling but far above the start
    for _ in range(6):
        reading(1.3)
    for temp in (4.0, 6.5, 7.2, 7.7, 7.8, 7.9, 7.9, 7.9, 7.9, 7.9, 7.9, 7.8, 7.9):
        reading(temp)
    check(f"open pushed ({pushes})", pushes == ["Fridge door open?"])
    check("peak kept in the state", store.get_state("fridge_door_rise").endswith("|1.3|7.9"))
    reading(7.6)
    check("a small dip: still open", pushes == ["Fridge door open?"] and fridge.door_state()["door_open"])
    reading(7.3)
    check(f"cooling 0.6 °C below the peak: closed ({pushes})",
          pushes == ["Fridge door open?", "Fridge door closed"] and not fridge.door_state()["door_open"]
          and store.get_state("fridge_door_rise") == "")
    reading(6.1)
    reading(6.0)
    check("cooling on: no new rise", store.get_state("fridge_door_rise") == "" and len(pushes) == 2)

    # A rise saved before #411 ("start|base") still reads; its peak starts from the base
    store.set_state("fridge_door_rise", f"{(t[0] - timedelta(minutes=30)):%Y-%m-%d %H:%M:%S}|1.3")
    reading(6.0)
    check("old format: read, peak from here", store.get_state("fridge_door_rise").endswith("|1.3|6.0"))
    reading(5.4)
    check("...and closed once it falls from there", store.get_state("fridge_door_rise") == "")
