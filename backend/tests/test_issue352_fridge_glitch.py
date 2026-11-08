"""#352: the DS18B20's 85 °C power-on value (and anything else outside FRIDGE_VALID) is
stored as null for the fridge, the rest of its reading kept; room nodes are unaffected."""
import json
import types

from support import check


def test_issue352_fridge_glitch(env):
    import server
    import ingest
    from store import db, get_state
    server.prepare()

    def mqtt(payload):
        ingest.on_message(None, None, types.SimpleNamespace(
            retain=False, topic="home/esp32/fridge_temp", payload=json.dumps(payload).encode()))

    def last(device):
        with db() as conn:
            return conn.execute("SELECT temp, alert, uptime FROM readings WHERE device = ? ORDER BY id DESC LIMIT 1",
                                (device,)).fetchone()

    fridge = {"device": "fridge_temp_esp32", "alert": "", "uptime": 100}
    mqtt({**fridge, "temp_c": 3.9})
    check("a normal reading is stored", last("fridge_temp_esp32") == (3.9, "", 100))

    mqtt({**fridge, "temp_c": 85.0, "uptime": 160})
    check("85.0 °C: temp null, the rest kept", last("fridge_temp_esp32") == (None, "", 160))
    check("...and no door rise from it", get_state("fridge_door_rise", "") == "")

    for bad in (-40.0, 51.0, "4.0"):
        mqtt({**fridge, "temp_c": bad})
        check(f"{bad!r}: temp null", last("fridge_temp_esp32")[0] is None)
    mqtt({**fridge, "temp_c": -30})
    check("the range's edge is kept", last("fridge_temp_esp32")[0] == -30)

    ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/bme280", payload=json.dumps(
        {"device": "home_ping_esp32", "temp_c": 60.0, "humidity": 40.0}).encode()))
    check("a room node's 60 °C isn't touched by the fridge range", last("home_ping_esp32")[0] == 60.0)
