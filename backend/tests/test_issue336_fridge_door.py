"""#336: the fridge door left open: a fast rise that hasn't cooled back after 10 min pushes
once, a door shut quickly pushes nothing, "closed" follows a sent push, muted is respected."""
import json
import types
from datetime import timedelta

from support import check


def test_issue336_fridge_door(env):
    import server
    import alerts, config, fridge, ingest, store
    from store import db, utcnow
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushes = []
    m.send.side_effect = lambda msg: pushes.append(msg["data"]["title"]) or "id"
    t0 = utcnow().replace(microsecond=0) - timedelta(hours=3)
    clock = {"t": t0}

    def reading(temp, minutes=1):
        """One fridge reading [minutes] after the last one, then the door check at that time."""
        clock["t"] += timedelta(minutes=minutes)
        ts = clock["t"]
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp) VALUES (?, ?, ?)",
                         (config.FRIDGE_DEVICE, ts.strftime("%Y-%m-%d %H:%M:%S"), temp))
        return fridge.check_door(now=ts)

    # --- ingest checks the door on every fridge reading (and only then)
    calls = []
    saved = ingest.check_door
    ingest.check_door = lambda: calls.append(1)
    mqtt = lambda topic, p: ingest.on_message(None, None, types.SimpleNamespace(
        retain=False, topic=topic, payload=json.dumps(p).encode()))
    mqtt("home/esp32/fridge_temp", {"device": config.FRIDGE_DEVICE, "temp_c": 3.0, "alert": ""})
    mqtt("home/esp32/env", {"device": "home_ping_esp32", "temp": 21.0, "humidity": 45})
    ingest.check_door = saved
    check(f"checked on the fridge reading only ({len(calls)})", calls == [1])
    with db() as conn:
        conn.execute("DELETE FROM readings")

    # --- the rule on its own
    T = lambda mins: t0 + timedelta(minutes=mins)
    check("slow warming: no rise", fridge.door_step(None, [(T(0), 3.0), (T(5), 4.5)]) == (None, None))
    rise, ev = fridge.door_step(None, [(T(0), 3.0), (T(2), 5.5)])
    check("fast rise watched", rise == (T(0), 3.0, 5.5) and ev is None)
    check("still up after 10 min: open", fridge.door_step(rise, [(T(10), 6.0)]) == ((T(0), 3.0, 6.0), "open"))
    check("cooled back: closed", fridge.door_step(rise, [(T(6), 3.9)]) == (None, "closed"))
    rise, _ = fridge.door_step(None, [(T(0), 3.0), (T(1), 3.0), (T(2), 3.0), (T(3), 5.5)])
    check(f"counted from the last low reading ({rise})", rise == (T(2), 3.0, 5.5))

    # --- through ingest: steady, then a door shut after 3 min
    for _ in range(6):
        reading(3.0)
    reading(5.5); reading(6.0); reading(4.5); reading(3.5)
    check(f"quick open: no push ({pushes})", pushes == [] and store.get_state("fridge_door_rise") == "")

    # --- left open: one push at ~10 min, then "closed" once it cools back
    for _ in range(6):
        reading(3.0)
    for temp in (5.5, 6.5, 7.0, 7.2, 7.4, 7.5, 7.6, 7.6, 7.7, 7.8, 7.8, 7.9):
        reading(temp)
    check(f"open pushed once ({pushes})", pushes == ["Fridge door open?"])
    reading(6.0); reading(4.5); reading(3.8)
    check(f"closed pushed ({pushes})", pushes == ["Fridge door open?", "Fridge door closed"])
    with db() as conn:
        types_ = [r[0] for r in conn.execute("SELECT type FROM events WHERE type LIKE 'FRIDGE_DOOR%' ORDER BY id")]
    check(f"events ({types_})", types_ == ["FRIDGE_DOOR_OPEN", "FRIDGE_DOOR_CLOSED"])

    # --- muted (switch off): no push either way
    store.set_state("notify:fridge", "0")
    for _ in range(6):
        reading(3.0)
    for temp in [5.5] + [7.0] * 11:
        reading(temp)
    check("muted: saved as muted", store.get_state("fridge_door") == "muted" and len(pushes) == 2)
    reading(3.5)
    check("muted: no closed push", len(pushes) == 2 and store.get_state("fridge_door") == "")


def test_issue393_door_through_quiet_hours(env):
    """#393: quiet hours don't mute the door push (open or closed); the fridge switch still does."""
    import server
    import alerts, config, fridge, store
    from store import db, utcnow
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushes = []
    m.send.side_effect = lambda msg: pushes.append(msg["data"]["title"]) or "id"
    alerts.in_quiet_hours = lambda now=None: store.get_state("quiet_hours", "0") == "1"   # always inside
    store.set_state("quiet_hours", "1")
    clock = {"t": utcnow().replace(microsecond=0) - timedelta(hours=1)}

    def reading(temp):
        clock["t"] += timedelta(minutes=1)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp) VALUES (?, ?, ?)",
                         (config.FRIDGE_DEVICE, clock["t"].strftime("%Y-%m-%d %H:%M:%S"), temp))
        fridge.check_door(now=clock["t"])

    def open_and_close():
        for _ in range(6):
            reading(3.0)
        for temp in [5.5] + [7.0] * 11:
            reading(temp)
        opened = list(pushes)
        reading(3.5)
        return opened

    check("quiet hours: other alerts muted", alerts.alert_muted("fridge") == "quiet hours")
    opened = open_and_close()
    check(f"quiet hours: open pushed ({opened})", opened == ["Fridge door open?"])
    check(f"...and closed ({pushes})", pushes == ["Fridge door open?", "Fridge door closed"])
    check("state sent, then over", store.get_state("fridge_door") == "")

    store.set_state("notify:fridge", "0")
    del pushes[:]
    open_and_close()
    check(f"switch off: nothing pushed ({pushes})", pushes == [])
    with db() as conn:
        last = conn.execute("SELECT value FROM events WHERE type = 'FRIDGE_DOOR_OPEN' ORDER BY id DESC LIMIT 1").fetchone()[0]
    check(f"...logged as muted (off) ({last})", last.startswith("muted (off)"))
