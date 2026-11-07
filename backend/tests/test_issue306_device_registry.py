"""#306 (R4): config.DEVICES names the nodes and their role; /api/devices serves it and
/api/latest_all marks the room devices."""
import base64

from support import check


def test_issue306_device_registry(env):
    import server, api, config, fridge
    from store import db
    server.prepare()

    check("derived from the registry",
          config.PRESENCE_DEVICE == "home_ping_esp32" and config.AIR_DEVICE == "home_ping_esp32"
          and config.NON_ROOM_DEVICES == ("fridge_temp_esp32",) and fridge.DEVICE == "fridge_temp_esp32")
    check("unknown device: room, named by its ID",
          config.device_info("outdoor_x") == {"name": "outdoor_x", "role": "room"})

    with db() as conn:
        for d in ("home_ping_esp32", "fridge_temp_esp32", "outdoor_x"):
            conn.execute("INSERT INTO devices (device, last_seen) VALUES (?, datetime('now'))", (d,))
            conn.execute("INSERT INTO readings (device, ts, temp) VALUES (?, datetime('now'), 20)", (d,))
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()
    devs = {d["device"]: d for d in client.get("/api/devices", headers=auth).get_json()}
    check("/api/devices: name and role", devs["fridge_temp_esp32"]["name"] == "Fridge"
          and devs["fridge_temp_esp32"]["role"] == "fridge" and devs["home_ping_esp32"]["role"] == "room"
          and devs["outdoor_x"]["name"] == "outdoor_x" and devs["home_ping_esp32"]["online"] is True)
    latest = client.get("/api/latest_all", headers=auth).get_json()
    check("/api/latest_all: room flag", latest["home_ping_esp32"]["room"] is True
          and latest["fridge_temp_esp32"]["room"] is False and latest["outdoor_x"]["room"] is True)
    check("combined has none", "room" not in latest["combined"])
