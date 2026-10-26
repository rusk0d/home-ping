"""GET /api/metrics: Prometheus text with node health from the presence messages, the newest
sensor values and the backend's state, behind its own bearer token (404 without one).
"""

import json
import os
import sqlite3
import time
from types import SimpleNamespace

from support import check


def test_metrics(env):
    os.environ["METRICS_TOKEN"] = "t0ken"
    import server
    import config, ingest, state, api
    server.prepare()
    c = api.app.test_client()

    def get(token="t0ken"):
        return c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"} if token else {})

    check("no token: 401", get(None).status_code == 401)
    check("wrong token: 401", get("nope").status_code == 401)
    check("Basic login doesn't open it", c.get("/api/metrics", headers={"Authorization": "Basic dTpw"}).status_code == 401)

    def msg(payload):
        ingest.on_message(None, None, SimpleNamespace(topic="home/esp32/presence", retain=False,
                                                      payload=json.dumps(payload).encode()))

    msg({"device": "home_ping_esp32", "presence": True, "moving": True, "radar_connected": True, "rssi": -58,
         "uptime": 3600, "reset_reason": 1, "heap_free": 148220, "heap_min": 121884, "heap_block": 110580})
    msg({"device": 'od"d\\node', "presence": False, "rssi": -70, "uptime": 60})   # older firmware: no heap fields
    conn = sqlite3.connect(config.DB)
    conn.execute("INSERT INTO readings (device, ts, temp, humidity, pressure) VALUES "
                 "('home_ping_esp32', datetime('now', '-60 seconds'), 22.5, 45.0, 1006.4)")
    conn.execute("INSERT INTO readings (device, ts, temp) VALUES ('home_ping_esp32', datetime('now', '-2 hours'), 30.0)")
    conn.execute("INSERT INTO readings (device, ts, co2, co2_ok) VALUES ('home_ping_esp32', datetime('now', '-30 seconds'), 950, 1)")
    conn.execute("INSERT INTO readings (device, ts, co2, co2_ok) VALUES ('home_ping_esp32', datetime('now'), 5000, 0)")
    conn.commit()

    r = get()
    body = r.get_data(as_text=True)
    lines = body.splitlines()
    check("200, Prometheus text format", r.status_code == 200 and r.content_type.startswith("text/plain; version=0.0.4"))
    for line in ('homeping_node_heap_free_bytes{device="home_ping_esp32"} 148220',
                 'homeping_node_heap_min_free_bytes{device="home_ping_esp32"} 121884',
                 'homeping_node_heap_max_block_bytes{device="home_ping_esp32"} 110580',
                 'homeping_node_uptime_seconds{device="home_ping_esp32"} 3600',
                 'homeping_node_wifi_rssi_dbm{device="home_ping_esp32"} -58',
                 'homeping_node_reset_reason{device="home_ping_esp32"} 1',
                 'homeping_occupied{device="home_ping_esp32"} 1',
                 'homeping_radar_connected{device="home_ping_esp32"} 1',
                 'homeping_temperature_celsius{device="home_ping_esp32"} 22.5',
                 'homeping_pressure_hpa{device="home_ping_esp32"} 1006.4',
                 'homeping_co2_ppm{device="home_ping_esp32"} 950',
                 'homeping_mqtt_connected 0'):
        check(f"has {line}", line in lines)
    check("each family has HELP and TYPE", "# TYPE homeping_node_heap_free_bytes gauge" in lines
          and "# HELP homeping_co2_ppm CO2 (MH-Z19C), valid readings only" in lines)
    check("device label escaped", 'homeping_node_uptime_seconds{device="od\\"d\\\\node"} 60' in lines)
    check("no heap fields: no heap sample for that node",
          not any(ln.startswith("homeping_node_heap_free_bytes{device=\"od") for ln in lines))
    check("last seen age per node", any(ln.startswith('homeping_node_last_seen_age_seconds{device="home_ping_esp32"}') for ln in lines))
    check("backend uptime and MQTT message age", any(ln.startswith("homeping_backend_uptime_seconds ") for ln in lines)
          and any(ln.startswith("homeping_mqtt_last_message_age_seconds ") for ln in lines))

    # A node that went quiet drops out instead of showing its last numbers forever
    state.NODE_HEALTH["home_ping_esp32"]["at"] = time.time() - config.METRICS_FRESH_SECONDS - 1
    lines = get().get_data(as_text=True).splitlines()
    check("stale node health left out", not any('heap_free_bytes{device="home_ping_esp32"}' in ln for ln in lines))


def test_metrics_off_without_token(env):
    os.environ.pop("METRICS_TOKEN", None)
    import server, api
    server.prepare()
    check("no METRICS_TOKEN: 404", api.app.test_client().get("/api/metrics").status_code == 404)
