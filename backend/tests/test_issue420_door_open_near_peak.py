"""#420: "Fridge door open?" only while the reading is still near the rise's peak. On 11-11 it
was pushed at 6.4 °C, already about 0.4 °C below the peak and cooling, and "closed" followed a
minute later."""

from datetime import datetime, timedelta

from support import check


def test_issue420_door_open_near_peak(env):
    import server
    import alerts, fridge, store
    from config import FRIDGE_DEVICE
    from store import db
    server.prepare()

    # --- the rule on its own
    T0 = datetime(2026, 11, 11, 18, 20)
    T = lambda mins: T0 + timedelta(minutes=mins)
    rise = (T(0), 2.1, 6.8)
    check("10 min, at the peak: open", fridge.door_step(rise, [(T(10), 6.8)]) == (rise, "open"))
    check("10 min, 0.2 below: open", fridge.door_step(rise, [(T(10), 6.6)]) == (rise, "open"))
    check("10 min, 0.4 below and cooling: not yet, still watched",
          fridge.door_step(rise, [(T(10), 6.4)]) == (rise, None))
    check("a new peak counts as near", fridge.door_step(rise, [(T(11), 7.0)]) == ((T(0), 2.1, 7.0), "open"))

    # --- through check_door: the 11-11 shape gets no push at all
    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append(title) or True
    t = [store.utcnow() - timedelta(hours=1)]

    def reading(temp):
        t[0] += timedelta(minutes=1)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, uptime) VALUES (?, ?, ?, 3600)",
                         (FRIDGE_DEVICE, t[0].strftime("%Y-%m-%d %H:%M:%S"), temp))
        fridge.check_door(now=t[0])

    for _ in range(6):
        reading(2.1)
    for temp in (4.5, 5.6, 6.2, 6.6, 6.8, 6.8, 6.7, 6.6, 6.5, 6.4):   # peak at 5 min, cooling from 8
        reading(temp)
    check(f"cooling at 10 min: no push ({pushes})", pushes == [] and store.get_state("fridge_door_rise") != "")
    reading(6.3)
    check(f"...and the rise closes without one ({pushes})", pushes == [] and store.get_state("fridge_door_rise") == "")

    # --- a door really left open: it dips, then comes back near the peak, and pushes then
    for _ in range(6):
        reading(2.0)
    for temp in (4.4, 5.5, 6.1, 6.5, 6.7, 6.8, 6.8, 6.7, 6.6, 6.5, 6.6, 6.7):
        reading(temp)
    check(f"back near the peak after 10 min: pushed ({pushes})", pushes == ["Fridge door open?"])
