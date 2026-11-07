"""#294: fridge pushes once per state change, with a per-state cooldown; a failed push is
retried instead of lost; a bad `alert` field doesn't drop the reading."""
import json
import time
import types

from support import check


def test_issue294_fridge_alerts(env):
    import server
    import config, state, alerts, ingest, fridge
    from store import db, get_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    events = []   # the data["alert"] / data["event"] of each accepted push

    def accept(msg):
        check("one notification slot for alert and recovery (#300)", msg["data"]["event"] == "FRIDGE_ALERT")
        events.append(msg["data"]["alert"] or "normal")
        return "id"
    m.send.side_effect = accept

    def reading(alert):
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, alert) VALUES "
                         "('fridge_temp_esp32', datetime('now'), 6.0, ?)", (alert,))

    t = time.time()

    reading("")
    fridge.check_fridge_alerts(t)
    check("normal from the start: no push", events == [])

    reading("warm_low")
    fridge.check_fridge_alerts(t)
    check("alert pushed", events == ["warm_low"] and get_state("fridge_alert_state") == "warm_low")
    fridge.check_fridge_alerts(t + 300)
    check("same state: no second push", events == ["warm_low"])

    reading("warm_critical")
    fridge.check_fridge_alerts(t + 600)
    check("escalation pushes at once", events == ["warm_low", "warm_critical"])

    reading("")
    fridge.check_fridge_alerts(t + 900)
    check("recovery pushed", events[-1] == "normal" and get_state("fridge_alert_state") == "")

    reading("warm_critical")
    fridge.check_fridge_alerts(time.time())
    check("back within the cooldown: held", events[-1] == "normal"
          and get_state("fridge_alert_state") == "")
    fridge.check_fridge_alerts(time.time() + config.NOTIFICATION_COOLDOWN + 1)
    check("after the cooldown: pushed", events[-1] == "warm_critical")

    # --- a failed push is retried, not lost
    state.LAST_NOTIFICATION.clear()
    reading("")
    fridge.check_fridge_alerts(time.time())
    reading("cold")
    m.send.side_effect = Exception("FCM down")
    fridge.check_fridge_alerts(time.time())
    check("failed push: state not saved", get_state("fridge_alert_state") == "")
    m.send.side_effect = accept
    fridge.check_fridge_alerts(time.time())
    check("retry waits for the backoff", events[-1] != "cold")
    fridge.check_fridge_alerts(time.time() + config.PUSH_RETRY_SECONDS[0] + 1)
    check("retried after the backoff", events[-1] == "cold" and get_state("fridge_alert_state") == "cold")

    # --- a bad alert field: reading kept, alert null
    ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/fridge_temp",
        payload=json.dumps({"device": "fridge_temp_esp32", "temp_c": 4.2, "alert": {"x": 1}}).encode()))
    with db() as conn:
        row = conn.execute("SELECT temp, alert FROM readings ORDER BY id DESC LIMIT 1").fetchone()
    check("bad alert: reading stored, alert null", row == (4.2, None))
