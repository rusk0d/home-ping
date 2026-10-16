"""#117: humidity/temperature alerts once per excursion, with hysteresis.
"""

from support import BACKEND, check


def test_issue117_alert_once_per_excursion(env):
    backend = str(BACKEND)
    import os, sys, json, tempfile, types
    from unittest import mock
    import server, alerts, ingest, state, store
    server.prepare()

    pushes = []
    ok = [True]
    alerts.send_push = lambda title, body, data=None: pushes.append(data["event"]) or ok[0]
    DEV = "home_ping_esp32"
    def env(**v):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/bme280",
            payload=json.dumps({"device": DEV, "uptime": 5, **v}).encode()))
    def cooldown_over():
        """Pretend the 15-min cooldown has passed (the flag must hold the alert back on its own)."""
        for k in list(state.LAST_NOTIFICATION):
            state.LAST_NOTIFICATION[k] -= 3600

    # --- humidity high
    env(humidity=70.0)
    check("high: first reading pushes", pushes == ["HUMIDITY_HIGH"])
    check("high: excursion flag set", store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "high")
    for h in (71.0, 75.0, 66.0):
        cooldown_over(); env(humidity=h)
    check("still high after the cooldown: no repeat", pushes == ["HUMIDITY_HIGH"])
    cooldown_over(); env(humidity=64.0); env(humidity=66.0)
    check("dip to 64 (inside the 2 % margin) and back: still no repeat", pushes == ["HUMIDITY_HIGH"])
    env(humidity=62.0)
    check("below 63: excursion over, flag cleared", store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "")
    import time
    state.LAST_NOTIFICATION[f"HUMIDITY_{DEV}"] = time.time() - 60   # last push 1 min ago
    env(humidity=67.0)
    check("new excursion inside the cooldown: waits for the cooldown", pushes == ["HUMIDITY_HIGH"])
    cooldown_over(); env(humidity=67.0)
    check("new excursion after the cooldown: pushes again", pushes == ["HUMIDITY_HIGH", "HUMIDITY_HIGH"])

    # --- restart keeps the flag
    server.prepare()
    cooldown_over(); env(humidity=70.0)
    check("after a backend restart: still no repeat", pushes.count("HUMIDITY_HIGH") == 2)

    # --- humidity low, and a swing from high straight to low
    pushes.clear(); cooldown_over()
    env(humidity=20.0)
    check("high -> low: the low alert pushes", pushes == ["HUMIDITY_LOW"] and store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "low")
    cooldown_over(); env(humidity=26.0); env(humidity=22.0)
    check("26 (inside the margin) and back: no repeat", pushes == ["HUMIDITY_LOW"])
    env(humidity=28.0)
    check("above 27: low excursion over", store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "")

    # --- temperature
    pushes.clear()
    env(temp_c=17.0)
    check("cold: pushes once", pushes == ["TEMP"])
    cooldown_over(); env(temp_c=16.0); cooldown_over(); env(temp_c=18.3)
    cooldown_over(); env(temp_c=17.5)
    check("stays cold / 18.3 (inside the 0.5 margin): no repeat", pushes == ["TEMP"])
    env(temp_c=18.6)
    check("above 18.5: excursion over", store.get_state(f"env_alerted:TEMP_{DEV}", "") == "")
    cooldown_over(); env(temp_c=17.9)
    check("cold again: new push", pushes == ["TEMP", "TEMP"])

    # --- a failed send sets no flag, so the 60 s retry still alerts
    pushes.clear(); env(temp_c=20.0); state.LAST_NOTIFICATION.clear()
    ok[0] = False
    env(humidity=80.0)
    check("failed send: no flag", store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "")
    ok[0] = True
    for k in list(state.LAST_NOTIFICATION): state.LAST_NOTIFICATION[k] -= 61
    env(humidity=80.0)
    check("retry after 60 s pushes and sets the flag", pushes == ["HUMIDITY_HIGH", "HUMIDITY_HIGH"]
          and store.get_state(f"env_alerted:HUMIDITY_{DEV}", "") == "high")

    # --- devices are independent
    pushes.clear()
    ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/bme280",
        payload=json.dumps({"device": "other_node", "humidity": 80.0, "uptime": 5}).encode()))
    check("another device has its own excursion", pushes == ["HUMIDITY_HIGH"])
