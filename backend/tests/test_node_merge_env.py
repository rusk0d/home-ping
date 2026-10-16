"""Node merge transition (#57, #11): the BME280 moved to the CO2 board.

The old main node, its sensor gone but not rebooted, sends 178 °C / 100 % / -121 hPa;
the CO2 board sends env and CO2 in separate messages as co2_sensor_esp32.
"""
import base64
import json
import logging
import types

from support import check


def test_node_merge_env(env, caplog):
    import server, api, ingest, alerts, store
    server.prepare()
    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append(data["event"]) or True
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    get = lambda path: c.get(path, headers=auth).get_json()

    def one(sql):
        with store.db() as conn:
            return conn.execute(sql).fetchone()

    def msg(topic, **payload):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic,
                          payload=json.dumps(payload).encode()))

    # Before the move: a valid reading from the old main node
    msg("home/esp32/bme280", device="home_ping_esp32", temp_c=22.0, humidity=45.0, pressure=1012.0, uptime=600000)

    # The old node's disconnected sensor
    with caplog.at_level(logging.WARNING, logger="ingest"):
        msg("home/esp32/bme280", device="home_ping_esp32", temp_c=178.37, humidity=100.0, pressure=-121.0086, uptime=633535)
    row = one("SELECT temp, humidity, pressure, uptime FROM readings WHERE device='home_ping_esp32' ORDER BY id DESC LIMIT 1")
    check("garbage reading: env values dropped, row (uptime) kept", row == (None, None, None, 633535))
    check("garbage reading: warning logged", any("implausible env reading" in r.getMessage() for r in caplog.records))
    check("garbage reading: no humidity alert", "HUMIDITY_HIGH" not in pushes)
    msg("home/esp32/bme280", device="x", temp_c=21.0, humidity=40.0, pressure=200.0, uptime=5)
    check("one value out of range drops all three",
          one("SELECT temp, humidity FROM readings WHERE device='x'") == (None, None))

    # The CO2 board: env and CO2 in separate messages
    msg("home/esp32/bme280", device="co2_sensor_esp32", temp_c=24.2, humidity=34.3, pressure=1010.3, uptime=180)
    msg("home/esp32/co2", device="co2_sensor_esp32", co2_ok=True, warming=False, err=1, co2=605, uptime=180)
    msg("home/esp32/bme280", device="co2_sensor_esp32", temp_c=24.1, humidity=34.5, pressure=1010.29, uptime=210)

    a = get("/api/latest_all")
    co2node = a["co2_sensor_esp32"]
    check("latest_all: CO2 kept although the newest row is env", co2node["co2"] == 605 and co2node["co2_ok"] is True)
    check("latest_all: env from the newest env row", co2node["temp"] == 24.1 and co2node["pressure"] == 1010.29)
    check("latest_all: own timestamps", co2node["env_ts"] and co2node["co2_ts"] and co2node["ts"] == co2node["env_ts"])
    check("latest_all: old main node keeps its last valid env", a["home_ping_esp32"]["temp"] == 22.0
          and a["home_ping_esp32"]["uptime"] == 633535)

    latest = get("/api/latest")
    check("widget: temperature from the device that has it now", latest["temp"] == 24.1 and latest["humidity"] == 34.5)
    check("widget: that device's online state", latest["online"] is True)

    air = get("/api/air")
    check("air: plausible pressure", air["pressure"] == 1010.29 and air["co2"] == 605)

    hourly = get("/api/hourly")
    check("history: temperature from any device by default", hourly and 22.0 <= hourly[0]["avg_temp"] <= 24.2)
    one = get("/api/hourly?device=home_ping_esp32")
    check("history: ?device= still filters", one and one[0]["avg_temp"] == 22.0)

    # A CO2 node that never sent env (old layout) still works
    msg("home/esp32/co2", device="old_co2", co2_ok=False, warming=True, err=1, uptime=5)
    old = get("/api/latest_all")["old_co2"]
    check("old layout: no env, CO2 flag present", old["temp"] is None and old["env_ts"] is None
          and old["co2"] is None and old["co2_ok"] is False)
