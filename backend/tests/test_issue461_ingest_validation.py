"""Ingest validation: a value that isn't a number is stored as null (#461), and a device that
isn't in config.DEVICES is dropped, on the data topics and on the status topic (#462)."""
import base64
import json
import types

from support import check


def test_issue461_ingest_validation(env):
    import server, api, ingest, alerts, store
    server.prepare()
    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append(data["event"]) or True
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    def rows(sql):
        with store.db() as conn:
            return conn.execute(sql).fetchall()

    def raw(topic, payload):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=payload.encode()))

    def msg(topic, **payload):
        raw(topic, json.dumps(payload))

    # ---- #461: strings, bools and objects are null, the row (uptime) is kept
    msg("home/esp32/bme280", device="home_ping_esp32", temp_c="21.5", humidity=True, pressure={"a": 1}, uptime=10)
    msg("home/esp32/co2", device="home_ping_esp32", co2="900", co2_ok=True, uptime=11)
    got = rows("SELECT temp, humidity, pressure, co2, uptime FROM readings ORDER BY id")
    check("non-numbers stored as null", got == [(None, None, None, None, 10), (None, None, None, None, 11)])
    check("no text in the numeric columns", rows(
        "SELECT COUNT(*) FROM readings WHERE typeof(temp)='text' OR typeof(humidity)='text' "
        "OR typeof(pressure)='text' OR typeof(co2)='text'") == [(0,)])
    check("no push from a non-number", pushes == [])
    check("/api/air still answers", c.get("/api/air", headers=auth).status_code == 200)
    msg("home/esp32/bme280", device="home_ping_esp32", temp_c=21.5, humidity=40, pressure=1010.2, uptime=12)
    check("numbers stored as before", rows(
        "SELECT temp, humidity, pressure FROM readings ORDER BY id DESC LIMIT 1") == [(21.5, 40, 1010.2)])

    # ---- #462: unknown devices
    before = rows("SELECT COUNT(*) FROM readings"), rows("SELECT COUNT(*) FROM events"), rows("SELECT COUNT(*) FROM devices")
    msg("home/esp32/bme280", device="stranger", temp_c=45.0, humidity=99.0, pressure=1010.0, uptime=5)
    msg("home/esp32/presence", device="stranger", presence=True, uptime=5)
    for i in range(20):
        raw(f"home/esp32/made_up_{i}/status", "online")
    raw("home/esp32/home_ping_esp32/status", "x" * 5000)
    after = rows("SELECT COUNT(*) FROM readings"), rows("SELECT COUNT(*) FROM events"), rows("SELECT COUNT(*) FROM devices")
    check("unknown device and bad status: nothing stored", before == after)
    check("unknown device: no push", pushes == [])
    raw("home/esp32/home_ping_esp32/status", "offline")
    check("known device's status still logged", rows(
        "SELECT value FROM events WHERE type='STATUS' ORDER BY id DESC LIMIT 1") == [("offline",)])
