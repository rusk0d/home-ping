"""Prometheus metrics (GET /api/metrics): node health from the presence messages (uptime,
WiFi, reset reason, heap), the newest sensor values and the backend's own state, in the
Prometheus text format. Scraped by Prometheus on the home server over Tailscale; the format
is plain text, so no client library is needed. Names and labels: contracts/api.md.
"""

import time

import state
from config import METRICS_FRESH_SECONDS
from store import db, parse_ts, utcnow

# (metric, help, the NODE_HEALTH key it comes from)
NODE_GAUGES = (
    ("homeping_node_uptime_seconds", "Seconds since the node booted", "uptime"),
    ("homeping_node_wifi_rssi_dbm", "WiFi signal strength", "rssi"),
    ("homeping_node_reset_reason", "Why the node last booted (esp_reset_reason_t code)", "reset_reason"),
    ("homeping_node_heap_free_bytes", "Free heap", "heap_free"),
    ("homeping_node_heap_min_free_bytes", "Lowest free heap since boot", "heap_min"),
    ("homeping_node_heap_max_block_bytes", "Largest heap block that can be allocated", "heap_block"),
    ("homeping_radar_connected", "1 while the node's radar sends frames", "radar_connected"),
    ("homeping_occupied", "1 while the node's radar sees someone", "occupied"),
)
# (metric, help, readings column)
SENSOR_GAUGES = (
    ("homeping_temperature_celsius", "Temperature (BME280)", "temp"),
    ("homeping_humidity_percent", "Relative humidity (BME280)", "humidity"),
    ("homeping_pressure_hpa", "Air pressure (BME280)", "pressure"),
    ("homeping_co2_ppm", "CO2 (MH-Z19C), valid readings only", "co2"),
)


def note_node(device, **values):
    """The newest health values a node sent (presence handler); None values are left out."""
    state.NODE_HEALTH[device] = {"at": time.time(), **{k: v for k, v in values.items() if v is not None}}


def label(value):
    """A label value, escaped as the text format wants (device IDs are free text)."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def gauge(lines, name, help_text, samples):
    """One metric family: [samples] is [(device, value)]; nothing when there is none."""
    if not samples:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    for device, value in samples:
        lines.append(f'{name}{{device="{label(device)}"}} {value}')


def latest_sensors():
    """Each device's newest value per sensor column from the last METRICS_FRESH_SECONDS."""
    since = f"-{METRICS_FRESH_SECONDS} seconds"
    out = {}
    with db() as conn:
        for _, _, col in SENSOR_GAUGES:
            valid = "AND co2_ok = 1" if col == "co2" else ""
            rows = conn.execute(f"""
                SELECT device, {col} FROM readings r
                WHERE ts = (SELECT MAX(ts) FROM readings
                            WHERE device = r.device AND {col} IS NOT NULL {valid}
                              AND ts >= datetime('now', ?))
                  AND {col} IS NOT NULL {valid}
                GROUP BY device
            """, (since,)).fetchall()
            out[col] = sorted(rows)
    return out


def render():
    now = time.time()
    lines = []
    with db() as conn:
        seen = conn.execute("SELECT device, last_seen FROM devices ORDER BY device").fetchall()
    gauge(lines, "homeping_node_last_seen_age_seconds", "Seconds since the node's last message",
          [(d, int((utcnow() - parse_ts(ts)).total_seconds())) for d, ts in seen])
    fresh = {d: h for d, h in sorted(state.NODE_HEALTH.items()) if now - h["at"] < METRICS_FRESH_SECONDS}
    for name, help_text, key in NODE_GAUGES:
        gauge(lines, name, help_text, [(d, h[key]) for d, h in fresh.items() if key in h])
    sensors = latest_sensors()
    for name, help_text, col in SENSOR_GAUGES:
        gauge(lines, name, help_text, sensors[col])
    lines += [
        "# HELP homeping_backend_uptime_seconds Seconds since the backend started",
        "# TYPE homeping_backend_uptime_seconds gauge",
        f"homeping_backend_uptime_seconds {int(now - state.STARTED_AT)}",
        "# HELP homeping_mqtt_connected 1 while the backend is connected to the broker",
        "# TYPE homeping_mqtt_connected gauge",
        f"homeping_mqtt_connected {int(state.mqttc is not None and state.mqttc.is_connected())}",
    ]
    if state.LAST_MESSAGE_AT:
        lines += [
            "# HELP homeping_mqtt_last_message_age_seconds Seconds since the last live MQTT message",
            "# TYPE homeping_mqtt_last_message_age_seconds gauge",
            f"homeping_mqtt_last_message_age_seconds {int(now - state.LAST_MESSAGE_AT)}",
        ]
    return "\n".join(lines) + "\n"
