"""#170: /api/dashboard ignores its own query string; env payloads without device are dropped.
"""

from support import check


def test_issue170(env):
    import base64, json, logging, sqlite3, types
    import server, config, ingest, api
    server.prepare()
    warnings = []
    class _Warn(logging.Handler):
        def emit(self, r): warnings.append(r.getMessage())
    logging.getLogger("ingest").addHandler(_Warn(level=logging.WARNING))

    def mqtt(topic, payload):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(payload).encode()))
    db = lambda: sqlite3.connect(config.DB)

    # Env payloads without a usable device
    for bad in ({"temp_c": 21.0, "humidity": 40.0}, {"device": "", "temp_c": 21.0}, {"device": 7, "co2": 800},
                {"device": "x" * 256, "co2": 800}):
        mqtt("home/esp32/bme280", bad)
    check("no rows stored", db().execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0)
    check("no phantom device", db().execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0)
    check("each logged as a warning", sum("without a device" in w for w in warnings) == 4)
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 21.0, "humidity": 40.0, "uptime": 5})
    check("a normal payload is still stored", db().execute("SELECT device FROM readings").fetchall() == [("home_ping_esp32",)])

    # /api/dashboard?days=7&device=x gives the same parts as without parameters
    c = api.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    plain = c.get("/api/dashboard", headers=A).get_json()
    with_args = c.get("/api/dashboard?days=7&device=nope", headers=A).get_json()
    check("hourly part unchanged by ?days=7&device=", with_args["hourly"] == plain["hourly"] and len(plain["hourly"]) == 1)
    check("whole response unchanged", {k: v for k, v in with_args.items() if k != "devices"} ==
          {k: v for k, v in plain.items() if k != "devices"})
