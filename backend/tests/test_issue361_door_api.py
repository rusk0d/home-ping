"""#361: /api/latest_all's fridge entry says whether the door looks open (the #336 door push's
excursion), and since when; other devices don't have the fields."""
import base64
from datetime import timedelta

from support import check


def test_issue361_door_api(env):
    import server
    import api, fridge
    from store import db, set_state, utcnow
    server.prepare()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    latest = lambda: api.app.test_client().get("/api/latest_all", headers=auth).get_json()

    # A real door opening through check_door(): 3.5 °C, then 6.5 °C for 10+ min
    start = utcnow().replace(microsecond=0) - timedelta(minutes=14)
    with db() as conn:
        conn.execute("INSERT INTO readings (device, ts, temp) VALUES ('home_ping_esp32', datetime('now'), 21)")
    def reading(minutes, temp):
        ts = start + timedelta(minutes=minutes)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, alert) VALUES ('fridge_temp_esp32', ?, ?, '')",
                         (ts.strftime("%Y-%m-%d %H:%M:%S"), temp))
        return fridge.check_door(now=ts)

    reading(0, 3.5)
    out = latest()
    check(f"closed ({out['fridge_temp_esp32']})", out["fridge_temp_esp32"]["door_open"] is False
          and out["fridge_temp_esp32"]["door_open_since"] is None)
    check("only the fridge has the fields", "door_open" not in out["home_ping_esp32"])

    for minute in range(1, 6):
        reading(minute, 6.5)
    check("rising, not 10 min yet: still closed", latest()["fridge_temp_esp32"]["door_open"] is False)
    for minute in range(6, 12):
        event = reading(minute, 6.5)
    door = latest()["fridge_temp_esp32"]
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    check(f"open after 10 min ({event}, {door})", event == "open" and door["door_open"] is True
          and door["door_open_since"] == since)

    reading(12, 3.9)
    check("back near where it started: closed", latest()["fridge_temp_esp32"]["door_open"] is False)

    # Muted (quiet hours / switch) counts too
    set_state("fridge_door", "muted")
    set_state("fridge_door_rise", "2026-11-08 10:00:00|3.5")
    door = latest()["fridge_temp_esp32"]
    check("a muted push still shows open", door["door_open"] is True and door["door_open_since"] == "2026-11-08T10:00:00Z")
