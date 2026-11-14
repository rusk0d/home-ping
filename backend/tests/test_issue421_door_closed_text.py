"""#421: "Fridge door closed" says "Cooling down again, 12.8 °C now" when the rise ended by
cooling from its peak (#411) while still warm, and "Back to 3.6 °C" when it's back near where it
started."""

from datetime import timedelta

from support import check


def test_issue421_door_closed_text(env):
    import server
    import alerts, fridge, store
    from config import FRIDGE_DEVICE
    from store import db
    server.prepare()

    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append((title, body)) or True
    t = [store.utcnow() - timedelta(hours=2)]

    def reading(temp):
        t[0] += timedelta(minutes=1)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, uptime) VALUES (?, ?, ?, 3600)",
                         (FRIDGE_DEVICE, t[0].strftime("%Y-%m-%d %H:%M:%S"), temp))
        fridge.check_door(now=t[0])

    def loading(*temps):
        for _ in range(6):
            reading(1.8)
        for temp in temps:
            reading(temp)

    # The 11-12 loading: open at the peak, then cooling while still far above the start
    loading(6.2, 9.6, 11.1, 11.9, 12.4, 12.8, 13.0, 13.2, 13.2, 13.3, 13.3, 13.0)
    check("open pushed", [p[0] for p in pushes] == ["Fridge door open?"])
    reading(12.8)
    check(f"closed by cooling: says so ({pushes[-1]})",
          pushes[-1] == ("Fridge door closed", "Cooling down again, 12.8 °C now"))
    with db() as conn:
        detail = conn.execute("SELECT value FROM events WHERE type = 'FRIDGE_DOOR_CLOSED' ORDER BY id DESC").fetchone()[0]
    check(f"logged as cooling ({detail})", detail.endswith("temp=12.8 (cooling)"))

    # A door shut while the fridge drops straight back: "Back to"
    pushes.clear()
    loading(4.5, 5.5, 6.0, 6.3, 6.5, 6.6, 6.7, 6.8, 6.9, 7.0, 7.0)
    check("open pushed again", [p[0] for p in pushes] == ["Fridge door open?"])
    reading(2.6)
    check(f"back near the start: Back to ({pushes[-1]})", pushes[-1] == ("Fridge door closed", "Back to 2.6 °C"))
