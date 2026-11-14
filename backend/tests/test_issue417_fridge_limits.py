"""#417: the fridge node publishes its alert limits; the backend keeps the newest valid set and
takes the push texts, the reboot hold's normal range and its longest timer from it, and serves
it on /api/latest_all. Before the node has sent any, the defaults (config) apply."""

import base64
import json
import time
import types

from support import check


def test_issue417_fridge_limits(env):
    import server
    import alerts, api, fridge, ingest, state
    from store import db, get_state, set_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushed = []
    m.send.side_effect = lambda msg: pushed.append((msg["data"]["title"], msg["data"]["body"])) or "id"
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()

    def mqtt(**payload):
        payload = {"device": "fridge_temp_esp32", "uptime": 50000, "alert": "", **payload}
        ingest.on_message(None, None, types.SimpleNamespace(
            retain=False, topic="home/esp32/fridge_temp", payload=json.dumps(payload).encode()))

    def later():
        state.LAST_NOTIFICATION.clear()
        fridge.check_fridge_alerts(time.time())

    def api_limits():
        return client.get("/api/latest_all", headers=auth).get_json()["fridge_temp_esp32"]["limits"]

    new = {"cold": -1, "warm": 7, "hot": 8, "hysteresis": 0.5, "minutes": 30, "long_minutes": 120}

    # --- before the node sends any: the defaults, and the texts from them
    mqtt(temp_c=3.0)
    check("no limits yet: defaults served", api_limits() == fridge.FRIDGE_LIMITS_DEFAULT)
    check("default texts", fridge.message("cold", fridge.limits()) == ("Fridge too cold", "Below 0 °C for 30+ minutes")
          and fridge.message("warm_critical_duration", fridge.limits())[1] == "Above 7 °C for 2+ hours")

    # --- the node's limits: kept, served, and the push says them
    mqtt(temp_c=3.0, limits=new)
    check("kept", json.loads(get_state("fridge_limits")) == new)
    check(f"served ({api_limits()})", api_limits() == new)
    mqtt(temp_c=-1.5, alert="cold"); later()
    check(f"cold push from the node's limit ({pushed})", pushed == [("Fridge too cold", "Below -1 °C for 30+ minutes")])

    # --- bad sets are ignored; the last good one stays
    for bad in ({**new, "cold": 9}, {**new, "warm": True}, {k: v for k, v in new.items() if k != "hot"},
                {**new, "minutes": 0}, {**new, "long_minutes": 10}, "cold", None):
        mqtt(temp_c=3.0, limits=bad)
        check(f"bad limits ignored: {bad}", json.loads(get_state("fridge_limits")) == new)
    check("NaN ignored", fridge.valid_limits({**new, "hot": float("nan")}) is None)
    check("extra keys ignored", fridge.valid_limits({**new, "future": 1}) == new)
    set_state("fridge_limits", "{broken")
    check("a broken saved value falls back to the defaults", fridge.limits() == fridge.FRIDGE_LIMITS_DEFAULT)
    mqtt(temp_c=3.0, limits=new)
    check("...and the next good set replaces it", fridge.limits() == new)

    # --- the reboot hold reads the node's range and longest timer
    with db() as conn:
        conn.execute("DELETE FROM readings")
    set_state("fridge_alert_state", "")
    set_state("fridge_alert_shown", "")
    pushed.clear()
    longer = {**new, "warm": 6, "long_minutes": 180}
    mqtt(temp_c=7.5, alert="warm_critical_duration", limits=longer); later()
    pushed.clear()
    mqtt(temp_c=5.8, alert="", uptime=150 * 60)   # within 180 min, above 6 - 0.5
    later()
    check(f"young node outside the node's normal range: held ({pushed})",
          pushed == [] and get_state("fridge_alert_state") == "warm_critical_duration")
    mqtt(temp_c=5.4, alert="", uptime=160 * 60)   # inside it
    later()
    check(f"inside the range: back to normal ({pushed})", [t for t, _ in pushed] == ["Fridge back to normal"])
    check("a 3 h timer says hours", fridge.message("warm_critical_duration", longer)[1] == "Above 6 °C for 3+ hours")
