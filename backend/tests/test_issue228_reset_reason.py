"""#228: the node's reset reason is logged with RESTART and returned by /api/restarts.
"""

from support import check


def test_issue228_reset_reason(env):
    import base64, json, sqlite3, types
    import server
    import config, ingest, api
    server.prepare()

    def mqtt(topic, p):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(p).encode()))
    def restart_values(device):
        return [v for (v,) in sqlite3.connect(config.DB).execute(
            "SELECT value FROM events WHERE type = 'RESTART' AND device = ? ORDER BY id", (device,))]
    main, hall = "home_ping_esp32", "hallway_presence_esp32"
    presence = {"presence": False, "radar_connected": True}

    # Brownout on the hallway node, seen first on a presence message
    mqtt("home/esp32/presence", {**presence, "device": hall, "uptime": 9000, "reset_reason": 1})
    mqtt("home/esp32/presence", {**presence, "device": hall, "uptime": 4, "reset_reason": 9})
    check("presence: reason in the RESTART event", restart_values(hall) == ["uptime 9000s -> 4s, reason brownout (9)"])

    # Main node: software restart (WiFi timeout / OTA), first seen on the env message
    env = {"device": main, "temp_c": 21.0, "humidity": 50.0}
    mqtt("home/esp32/bme280", {**env, "uptime": 5000, "reset_reason": 1})
    mqtt("home/esp32/bme280", {**env, "uptime": 20, "reset_reason": 3})
    # ...then a node still on the old firmware (no reset_reason), and an unknown code
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 5000})
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 30})
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 5000})
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 40, "reset_reason": 42})
    check("env/co2: software, none, unknown code", restart_values(main) == [
        "uptime 5000s -> 20s, reason software (3)", "uptime 5000s -> 30s",
        "uptime 5000s -> 40s, reason code (42)"])

    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    r = c.get("/api/restarts", headers=auth).get_json()
    check("API: hallway last_reason brownout", r[hall]["last_reason"] == "brownout" and r[hall]["last_24h"] == 1)
    check("API: main last_reason from the newest restart", r[main]["last_reason"] == "code" and r[main]["last_24h"] == 3)

    # Newest restart without a reason (old firmware) -> null
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 5000})
    mqtt("home/esp32/co2", {"device": main, "co2_ok": False, "uptime": 50})
    r = c.get("/api/restarts", headers=auth).get_json()
    check("API: no reason on the newest restart -> null", r[main]["last_reason"] is None)
