"""#77: /api/health
"""

from support import check


def test_issue77_health(env):
    import os, sys, json, tempfile, types, time
    from unittest import mock
    import server
    import config, state, store, ingest, api
    server.prepare()   # importing does no work (#73)
    store.init_db()
    c = api.app.test_client()
    def get():
        r = c.get("/api/health"); return r.status_code, r.get_json()
    def msg(retain=False):
        ingest.on_message(None, None, types.SimpleNamespace(retain=retain, topic="home/esp32/presence",
            payload=json.dumps({"device": "home_ping_esp32", "presence": False, "moving": False,
                                "stationary": False, "signal": 0, "distance": 0}).encode()))

    state.mqttc = mock.MagicMock()   # created by start()
    state.mqttc.is_connected = lambda: True
    s, b = get()
    check("just started, no message yet -> 200", s == 200 and b["ok"] and b["last_message_age_s"] is None)
    check("no auth needed, no sensor data", set(b) == {"ok", "mqtt_connected", "last_message_age_s", "db_ok"})

    state.STARTED_AT = time.time() - 400
    s, b = get()
    check("no message 400 s after start -> 503", s == 503 and not b["ok"])

    msg(retain=True)
    check("retained replay doesn't count", get()[0] == 503)
    msg()
    s, b = get()
    check("live message -> 200, age 0", s == 200 and b["last_message_age_s"] == 0)

    state.LAST_MESSAGE_AT = time.time() - 301
    s, b = get()
    check("last message 301 s ago -> 503", s == 503 and b["last_message_age_s"] == 301)
    msg()

    state.mqttc.is_connected = lambda: False
    s, b = get()
    check("MQTT disconnected -> 503", s == 503 and b["mqtt_connected"] is False)
    state.mqttc.is_connected = lambda: True

    real = config.DB
    config.DB = os.path.join("nope", "x.db")
    s, b = get()
    check("DB unreadable -> 503", s == 503 and b["db_ok"] is False)
    config.DB = real
    check("back to 200", get()[0] == 200)
    check("other endpoints still need auth", c.get("/api/latest").status_code == 401)
