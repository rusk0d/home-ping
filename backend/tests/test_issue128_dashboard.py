"""#128: /api/dashboard = the six home-screen endpoints in one response."""
import base64
import json
import types

from support import check


def test_issue128_dashboard(env):
    import server, api, ingest
    server.prepare()
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    def msg(topic, **p):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(p).encode()))
    msg("home/esp32/bme280", device="home_ping_esp32", temp_c=22.5, humidity=41.0, pressure=1011.0, uptime=100)
    msg("home/esp32/co2", device="home_ping_esp32", co2_ok=True, warming=False, err=1, co2=650, uptime=100)
    msg("home/esp32/presence", device="home_ping_esp32", presence=True, moving=True, stationary=False,
        signal=60, distance=150, uptime=100, radar_connected=True)
    msg("home/esp32/presence", device="hallway_presence_esp32", presence=False, moving=False, stationary=False,
        signal=0, distance=0, uptime=50, radar_connected=True)

    r = c.get("/api/dashboard", headers=auth)
    check("200 with login", r.status_code == 200)
    d = r.get_json()
    check("the six parts", set(d) == {"latest_all", "devices", "stats24h_combined", "restarts", "air", "hourly"})
    for key, path in [("latest_all", "/api/latest_all"), ("devices", "/api/devices"),
                      ("stats24h_combined", "/api/stats24h_combined"), ("restarts", "/api/restarts"),
                      ("air", "/api/air"), ("hourly", "/api/hourly")]:
        check(f"{key} equals {path}", d[key] == c.get(path, headers=auth).get_json())
    check("content: CO2 and temperature", d["air"]["co2"] == 650 and d["latest_all"]["home_ping_esp32"]["temp"] == 22.5)
    check("needs the login", c.get("/api/dashboard").status_code == 401)
