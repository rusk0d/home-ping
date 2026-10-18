"""#226: a payload without uptime is not read as a restart.
"""

from support import check


def test_issue226_missing_uptime(env):
    import json, sqlite3, types
    import server
    import config, ingest
    server.prepare()

    def mqtt(topic, p):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(p).encode()))
    db = lambda q: sqlite3.connect(config.DB).execute(q).fetchall()
    restarts = lambda: db("SELECT COUNT(*) FROM events WHERE type = 'RESTART'")[0][0]
    presence = {"device": "hallway_presence_esp32", "presence": False, "radar_connected": True}

    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 21.0, "humidity": 50.0, "uptime": 5000})
    mqtt("home/esp32/presence", {**presence, "uptime": 5000})
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 21.0, "humidity": 50.0})
    mqtt("home/esp32/presence", presence)
    mqtt("home/esp32/presence", {**presence, "uptime": "n/a"})
    check("missing or bad uptime: no RESTART", restarts() == 0)
    check("env row stored with NULL uptime", db("SELECT uptime FROM readings ORDER BY id DESC LIMIT 1")[0][0] is None)
    check("presence row stored with NULL uptime",
          db("SELECT uptime FROM presence_readings ORDER BY id DESC LIMIT 1")[0][0] is None)

    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 21.0, "humidity": 50.0, "uptime": 10})
    check("a real reboot is still a RESTART", restarts() == 1)
