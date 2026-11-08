"""#359: /api/devices marks the nodes in the registry (config.DEVICES) as known; a retired one
still listed (until DEVICES_RETENTION_DAYS) isn't."""
import base64

from support import check


def test_issue359_known_devices(env):
    import server
    import api
    from nodes import touch_device
    server.prepare()
    for dev in ("home_ping_esp32", "fridge_temp_esp32", "co2_sensor_esp32"):
        touch_device(dev)
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    out = {d["device"]: d for d in api.app.test_client().get("/api/devices", headers=auth).get_json()}
    check(f"registry nodes known ({out})", out["home_ping_esp32"]["known"] is True and out["fridge_temp_esp32"]["known"] is True)
    check("a retired node isn't", out["co2_sensor_esp32"]["known"] is False and out["co2_sensor_esp32"]["role"] == "room")
