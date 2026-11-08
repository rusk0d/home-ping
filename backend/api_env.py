"""REST API, current readings: /api/latest, latest_all, stats24h_combined, air, devices,
restarts. A Flask blueprint (#306); contracts/api.md.
"""

import logging
import re

from flask import Blueprint, jsonify

from config import (
    DEVICES, MOULD_HOURS, MOULD_HUMIDITY, ROOM_DEVICES_SQL, device_info,
)
from store import (
    calculate_combined_confidence, calculate_confidence, db, get_combined_occupancy,
    occupancy_stats, reset_reason_text, utcnow,
)
from api_common import is_online, iso, require_auth
from metrics import fresh_health

log = logging.getLogger(__name__)
bp = Blueprint("env", __name__)


@bp.route("/api/latest")
@require_auth
def latest():
    """Latest temp/humidity + combined radar occupancy (widget).

    Temperature and humidity come from the newest reading of whichever device has them,
    like /api/air: during the node merge the BME280 moved boards (#57).
    Non-room devices (NON_ROOM_DEVICES, the fridge #290) are left out.
    """
    with db() as conn:
        c = conn.cursor()
        c.execute(f"""
            SELECT ts, temp, humidity, uptime, device
            FROM readings
            WHERE (temp IS NOT NULL OR humidity IS NOT NULL) AND {ROOM_DEVICES_SQL}
            ORDER BY id DESC LIMIT 1
        """)
        row = c.fetchone() or (None, None, None, None, None)
        c.execute("SELECT last_seen FROM devices WHERE device = ?", (row[4],))
        dev = c.fetchone()

    online = bool(dev) and is_online(dev[0])

    return jsonify({
        "ts": iso(row[0]),
        "temp": row[1],
        "humidity": row[2],
        "uptime": row[3],
        "online": online,
        "occupied": get_combined_occupancy(),
        "confidence": calculate_combined_confidence(),
    })


@bp.route("/api/latest_all")
@require_auth
def latest_all():
    """Latest readings from every device — env nodes and radar nodes."""
    with db() as conn:
        c = conn.cursor()

        now = utcnow()

        def device_online(device):
            c.execute("SELECT last_seen FROM devices WHERE device = ?", (device,))
            dev = c.fetchone()
            return bool(dev) and is_online(dev[0], now)

        result = {}

        # ---- Environmental readings ----
        # A node can send env and CO2 in separate messages (since #57 the CO2 board has
        # the BME280 too), so each comes from its own newest row, with its own timestamp (#11)
        def newest(device, cols, where):
            c.execute(f"SELECT ts, {cols} FROM readings WHERE device = ? AND ({where}) "
                      "ORDER BY id DESC LIMIT 1", (device,))
            return c.fetchone()

        c.execute("SELECT DISTINCT device FROM readings")
        for device in [row[0] for row in c.fetchall()]:
            last = newest(device, "uptime", "1")
            if not last:
                continue
            env = newest(device, "temp, humidity, pressure",
                         "temp IS NOT NULL OR humidity IS NOT NULL OR pressure IS NOT NULL")
            co2 = newest(device, "co2, co2_ok", "co2 IS NOT NULL OR co2_ok IS NOT NULL")
            alert = newest(device, "alert", "alert IS NOT NULL")

            result[device] = {
                "ts": iso(last[0]),
                "uptime": last[1],
                "online": device_online(device),
                "temp": env[1] if env else None,
                "humidity": env[2] if env else None,
                "pressure": env[3] if env else None,
                "env_ts": iso(env[0]) if env else None,
                "co2": co2[1] if co2 else None,
                "co2_ok": None if not co2 or co2[2] is None else bool(co2[2]),
                "co2_ts": iso(co2[0]) if co2 else None,
                "alert": alert[1] if alert else None,
                "alert_ts": iso(alert[0]) if alert else None,
            }

        # ---- Radar nodes: merge in, or add if they have no env readings ----
        c.execute("SELECT DISTINCT device FROM presence_readings")
        for device in [row[0] for row in c.fetchall()]:
            c.execute("""
                SELECT ts, occupied, presence, stationary, moving,
                       distance, signal, rssi, uptime, radar_connected
                FROM presence_readings
                WHERE device = ?
                ORDER BY id DESC LIMIT 1
            """, (device,))
            p = c.fetchone()
            if not p:
                continue

            entry = result.get(device, {
                "temp": None,
                "humidity": None,
                "co2": None,
                "pressure": None,
            })

            entry.update({
                "ts": iso(p[0]),
                "uptime": p[8],
                "occupied": p[1],
                "confidence": calculate_confidence(device),
                "online": device_online(device),
                "presence": p[2],
                "stationary": p[3],
                "moving": p[4],
                "distance": p[5],
                "signal": p[6],
                "rssi": p[7],
                "radar_connected": p[9],
            })
            result[device] = entry

    # Whether its readings are the room's (config.DEVICES, #306): the app's room card skips
    # the others instead of hard-coding the fridge
    for device, entry in result.items():
        entry["room"] = device_info(device)["role"] == "room"
    result["combined"] = {
        "occupied": get_combined_occupancy(),
        "confidence": calculate_combined_confidence()
    }

    return jsonify(result)


@bp.route("/api/stats24h_combined")
@require_auth
def stats24h_combined():
    '''Combined 24h env stats from all room devices + radar occupancy'''
    with db() as conn:
        c = conn.cursor()
        # Averaged in SQL (#123); AVG is NULL when there are no rows
        avg_temp, avg_hum = c.execute(f"""
            SELECT AVG(temp), AVG(humidity)
            FROM readings
            WHERE ts >= datetime('now','-24 hours') AND {ROOM_DEVICES_SQL}
              AND temp IS NOT NULL
              AND humidity IS NOT NULL
        """).fetchone()

    occ = occupancy_stats(24)

    return jsonify({
        "avg_temp": None if avg_temp is None else round(avg_temp, 1),
        "avg_humidity": None if avg_hum is None else round(avg_hum, 1),
        "avg_occupancy": occ["avg_occupancy"],
        "occupied_hours": occ["occupied_hours"],
        "coverage_hours": occ["coverage_hours"]
    })


@bp.route("/api/air")
@require_auth
def air():
    """Latest CO2 + pressure, with a simple quality band."""
    with db() as conn:
        c = conn.cursor()

        # CO2 — whichever device reports it
        c.execute(f"""
            SELECT ts, co2 FROM readings
            WHERE co2 IS NOT NULL AND {ROOM_DEVICES_SQL}
            ORDER BY id DESC LIMIT 1
        """)
        co2_row = c.fetchone()

        # Pressure + env — whichever device reports it
        c.execute(f"""
            SELECT ts, pressure, temp, humidity FROM readings
            WHERE pressure IS NOT NULL AND {ROOM_DEVICES_SQL}
            ORDER BY id DESC LIMIT 1
        """)
        env_row = c.fetchone()

        # Pressure as it was 3 hours ago
        c.execute(f"""
            SELECT pressure FROM readings
            WHERE pressure IS NOT NULL AND {ROOM_DEVICES_SQL}
              AND ts <= datetime('now','-3 hours')
            ORDER BY id DESC LIMIT 1
        """)
        old = c.fetchone()

        # Mould risk (#177): humidity per complete hour over the last MOULD_HOURS hours
        c.execute(f"""
            SELECT strftime('%Y-%m-%d %H', ts), AVG(humidity) FROM readings
            WHERE humidity IS NOT NULL AND {ROOM_DEVICES_SQL}
              AND ts >= datetime(strftime('%Y-%m-%d %H:00:00', 'now'), ?)
              AND ts < strftime('%Y-%m-%d %H:00:00', 'now')
            GROUP BY 1
        """, (f"-{MOULD_HOURS} hours",))
        humid_hours = [h for _, h in c.fetchall()]

    co2 = co2_row[1] if co2_row else None
    if co2 is None:        band = None
    elif co2 < 800:        band = "good"
    elif co2 < 1200:       band = "fair"
    elif co2 < 2000:       band = "stuffy"
    else:                  band = "bad"

    pressure = env_row[1] if env_row else None
    trend = None
    if old and pressure is not None:
        trend = round(pressure - old[0], 1)

    return jsonify({
        "ts": iso(env_row[0] if env_row else (co2_row[0] if co2_row else None)),
        "co2": co2,
        "band": band,
        "pressure": pressure,
        "pressure_3h_change": trend,
        "temp": env_row[2] if env_row else None,
        "humidity": env_row[3] if env_row else None,
        "mould_risk": len(humid_hours) == MOULD_HOURS and all(h > MOULD_HUMIDITY for h in humid_hours),
    })


HEALTH_FIELDS = ("rssi", "uptime", "heap_free", "heap_min", "heap_block")


def health(device):
    """What the node last reported about itself (#322), the same values as /api/metrics: only
    the fresh ones (a node that went quiet has none). reset_reason as text, e.g. "brownout"."""
    h = fresh_health(device)
    out = {k: h[k] for k in HEALTH_FIELDS if k in h}
    if "reset_reason" in h:
        out["reset_reason"] = reset_reason_text(h["reset_reason"]).rsplit(" (", 1)[0]
    if "radar_connected" in h:
        out["radar_connected"] = bool(h["radar_connected"])
    return out


@bp.route("/api/devices")
@require_auth
def devices():
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT device, last_seen FROM devices")
        rows = c.fetchall()

    now = utcnow()
    out = []

    for dev, ts in rows:
        out.append({
            "device": dev,
            **device_info(dev),   # name, role (config.DEVICES, #306)
            # In the registry: a node that's meant to be there. One that isn't (e.g. retired)
            # stays listed for DEVICES_RETENTION_DAYS, but its silence is no issue (#359)
            "known": dev in DEVICES,
            "last_seen": iso(ts),
            "online": is_online(ts, now),
            "health": health(dev),
        })

    return jsonify(out)


@bp.route("/api/restarts")
@require_auth
def restarts():
    """Restart counts per device, last 24h and 7d, plus the most recent."""
    with db() as conn:
        c = conn.cursor()

        c.execute("""
            SELECT device, COUNT(*)
            FROM events
            WHERE type = 'RESTART'
              AND ts >= datetime('now','-24 hours')
            GROUP BY device
        """)
        day = dict(c.fetchall())

        c.execute("""
            SELECT device, COUNT(*)
            FROM events
            WHERE type = 'RESTART'
              AND ts >= datetime('now','-7 days')
            GROUP BY device
        """)
        week = dict(c.fetchall())

        # The newest RESTART row per device (by id: several can share a second)
        c.execute("""
            SELECT device, ts, value
            FROM events
            WHERE id IN (SELECT MAX(id) FROM events WHERE type = 'RESTART' GROUP BY device)
        """)
        last = {device: (ts, value) for device, ts, value in c.fetchall()}

    def reason(value):
        # "uptime 86400s -> 12s, reason brownout (9)" (#228); older rows have no reason
        m = re.search(r", reason (.+) \(\d+\)$", value or "")
        return m.group(1) if m else None

    devices = set(day) | set(week) | set(last)
    return jsonify({
        d: {
            "last_24h": day.get(d, 0),
            "last_7d": week.get(d, 0),
            "last_restart": iso(last.get(d, (None, None))[0]),
            "last_reason": reason(last.get(d, (None, None))[1]),
        } for d in devices
    })
