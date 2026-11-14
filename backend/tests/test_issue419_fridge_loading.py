"""#419: "Loading the fridge" holds the warm alert and door pushes for the chosen time; whatever
is still on afterwards is pushed then. Cold isn't held. POST /api/fridge/loading."""

import base64
from datetime import timedelta

from support import check


def test_issue419_fridge_loading(env):
    import server
    import alerts, api, fridge, state, store
    from config import FRIDGE_DEVICE
    from store import db, get_state, set_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushed = []
    m.send.side_effect = lambda msg: pushed.append(msg["data"]["title"]) or "id"
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()
    post = lambda body: client.post("/api/fridge/loading", json=body, headers=auth)
    t = [store.utcnow() - timedelta(hours=1)]

    def reading(temp, alert=""):
        t[0] += timedelta(minutes=1)
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, alert, uptime) VALUES (?, ?, ?, ?, 50000)",
                         (FRIDGE_DEVICE, t[0].strftime("%Y-%m-%d %H:%M:%S"), temp, alert))
        fridge.check_door(now=t[0])

    def check_alerts():
        state.LAST_NOTIFICATION.clear()
        fridge.check_fridge_alerts()

    def held_events():
        with db() as conn:
            return [r[0] for r in conn.execute("SELECT value FROM events WHERE type = 'FRIDGE_LOADING' ORDER BY id")]

    # --- the endpoint
    for bad in ({"minutes": True}, {"minutes": "60"}, {"minutes": -1}, {"minutes": 181}, {"minutes": 1.5}, {}):
        check(f"rejected: {bad}", post(bad).status_code == 400)
    check("nothing on: no hold", fridge.loading_until() is None)
    r = post({"minutes": 60})
    until = r.get_json()["loading_until"]
    check(f"60 min: on ({until})", r.status_code == 200 and until and fridge.loading_until() is not None)
    reading(2.0)
    latest = client.get("/api/latest_all", headers=auth).get_json()[FRIDGE_DEVICE]
    check("served on latest_all", latest["loading_until"] == until)

    # --- loading: a fast rise and "too warm" are held, logged once each
    for _ in range(5):
        reading(2.0)
    for temp in (6.0, 9.0, 11.0, 12.0, 12.5, 12.8, 13.0, 13.1, 13.2, 13.3, 13.3, 13.3):
        reading(temp)
    check(f"door left open while loading: held ({pushed})", pushed == [])
    reading(13.3, "warm_critical")
    check_alerts()
    check_alerts()
    check(f"too warm while loading: held, not saved ({pushed})",
          pushed == [] and get_state("fridge_alert_state", "") == "")
    check(f"each logged once ({held_events()})", held_events()[1:] == ["held door", "held warm_critical"])

    # --- cold isn't held
    set_state("fridge_alert_state", "")
    reading(-1.5, "cold")
    check_alerts()
    check(f"cold while loading: pushed ({pushed})", pushed == ["Fridge too cold"])
    reading(2.0, "")
    check_alerts()
    pushed.clear()

    # --- ended early with "too warm" still on: pushed right away, and the door after it
    reading(13.3, "warm_critical")
    check_alerts()
    check("still held", pushed == [])
    r = post({"minutes": 0})
    check(f"ended: too warm pushed now ({pushed})", r.get_json() == {"loading_until": None}
          and pushed == ["Fridge too warm"] and get_state("fridge_alert_state") == "warm_critical")
    # The cold reading ended the earlier rise; this one started at it, so the push comes 10 min in
    for _ in range(10):
        reading(13.3, "warm_critical")
    check(f"door still open after it: pushed ({pushed})", pushed == ["Fridge too warm", "Fridge door open?"])

    # --- a hold that ran out on its own is over
    set_state("fridge_loading_until", (store.utcnow() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"))
    check("expired: no hold", fridge.loading_until() is None
          and client.get("/api/latest_all", headers=auth).get_json()[FRIDGE_DEVICE]["loading_until"] is None)
