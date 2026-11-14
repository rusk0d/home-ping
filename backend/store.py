"""SQLite: the db() helper, the writes (readings, events, the hourly roll-up, retention) and
system_state. The schema and its migrations are in schema.py, the occupancy queries in
occupancy.py (#425).
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config
import state
from config import (
    DEVICES_RETENTION_DAYS, EVENTS_RETENTION_DAYS, FRIDGE_DEVICE, PRESENCE_DEVICE, RAW_RETENTION_DAYS, ROOM_DEVICES_SQL,
    UPTIME_DROP_TOLERANCE,
)

log = logging.getLogger(__name__)


def utcnow():
    """Naive UTC now: comparable with the stored timestamps, which are UTC (#65)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_ts(ts):
    """A stored timestamp ("YYYY-MM-DD HH:MM:SS", UTC) as a naive datetime, like utcnow()."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


# -------------------------
# CONNECTION
# -------------------------
@contextmanager
def db():
    """A connection that is committed on success and always closed (#73).

    The database is in WAL mode (#174, set in schema.init_db): readers (API) and the writer
    (MQTT) don't block each other. synchronous=NORMAL is per connection; with WAL it is
    still safe against corruption, a power cut can only lose the last commits.
    """
    conn = sqlite3.connect(config.DB)
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_to_db(device, temp, hum, uptime, co2=None, pressure=None, co2_ok=None, alert=None):
    with db() as conn:
        c = conn.cursor()

        c.execute("""
            INSERT INTO readings
            (device, ts, temp, humidity, uptime, co2, pressure, co2_ok, alert)
            VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
        """, (device, temp, hum, uptime, co2, pressure, co2_ok, alert))


def log_presence(device, occupied, presence, stationary, moving,
                 distance, signal, rssi, uptime, radar_connected):
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO presence_readings
            (device, ts, occupied, presence, stationary, moving,
             distance, signal, rssi, uptime, radar_connected)
            VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (device, occupied, presence, stationary, moving,
              distance, signal, rssi, uptime, radar_connected))


def prune():
    """Retention (#18, #173): raw readings RAW_RETENTION_DAYS (so the 24 h queries always have
    full coverage), events EVENTS_RETENTION_DAYS, nodes unseen DEVICES_RETENTION_DAYS (e.g. a
    retired one). Once per summaries pass and at startup (#304), not on every insert, and
    always after rollup_hourly(): those raw rows must be in `hourly` before they go."""
    with db() as conn:
        for table, col, days in (("readings", "ts", RAW_RETENTION_DAYS),
                                 ("presence_readings", "ts", RAW_RETENTION_DAYS),
                                 ("events", "ts", EVENTS_RETENTION_DAYS),
                                 ("devices", "last_seen", DEVICES_RETENTION_DAYS)):
            conn.execute(f"DELETE FROM {table} WHERE {col} < datetime('now', ?)", (f"-{days} days",))


# A raw ts's hour as `hourly` stores it
_HOUR_SQL = "strftime('%Y-%m-%d %H:00:00', ts)"

HOURLY_COLUMNS = ("hour", "temp_avg", "temp_min", "temp_max", "humidity_avg", "humidity_min",
                  "humidity_max", "co2_avg", "co2_min", "co2_max", "pressure_avg", "pressure_min",
                  "pressure_max", "occupancy", "fridge_avg", "fridge_min", "fridge_max")
_OCCUPANCY = HOURLY_COLUMNS.index("occupancy")
_FRIDGE = HOURLY_COLUMNS.index("fridge_avg")


def rollup_hourly(now=None):
    """Aggregate complete hours from readings/presence_readings into `hourly` (#129): the room
    devices combined, the radar's occupancy and the fridge's temperature (#331).

    system_state 'hourly_until' is where the last run stopped (the then-current hour). A run
    starts an hour before it (redone, in case rows arrived late), or at the oldest raw row
    on the first run, and does nothing until another hour has completed, so it is cheap to
    call often. Returns the number of hours written.
    """
    end = (now or utcnow()).replace(minute=0, second=0, microsecond=0)   # current hour: not complete
    end_s = end.strftime("%Y-%m-%d %H:%M:%S")
    until = get_state("hourly_until", None)
    if until is not None and parse_ts(until) >= end:
        return 0
    since = (parse_ts(until) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S") if until else "0000-01-01 00:00:00"
    with db() as conn:
        hours = {}
        def row(h):
            return hours.setdefault(h, [h] + [None] * (len(HOURLY_COLUMNS) - 1))
        for r in conn.execute(f"""
            SELECT {_HOUR_SQL},
                   AVG(temp), MIN(temp), MAX(temp), AVG(humidity), MIN(humidity), MAX(humidity),
                   AVG(co2), MIN(co2), MAX(co2), AVG(pressure), MIN(pressure), MAX(pressure)
            FROM readings WHERE ts >= ? AND ts < ? AND {ROOM_DEVICES_SQL} GROUP BY 1
        """, (since, end_s)):
            row(r[0])[1:_OCCUPANCY] = r[1:]
        for h, occ in conn.execute(f"""
            SELECT {_HOUR_SQL}, AVG(occupied) * 100
            FROM presence_readings WHERE device = ? AND ts >= ? AND ts < ? GROUP BY 1
        """, (PRESENCE_DEVICE, since, end_s)):
            row(h)[_OCCUPANCY] = occ
        # The fridge on its own (#331), never in the room columns (#290)
        for r in conn.execute(f"""
            SELECT {_HOUR_SQL}, AVG(temp), MIN(temp), MAX(temp)
            FROM readings WHERE device = ? AND temp IS NOT NULL AND ts >= ? AND ts < ? GROUP BY 1
        """, (FRIDGE_DEVICE, since, end_s)):
            row(r[0])[_FRIDGE:_FRIDGE + 3] = r[1:]
        conn.executemany(
            f"INSERT OR REPLACE INTO hourly ({', '.join(HOURLY_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(HOURLY_COLUMNS))})",
            hours.values())
    set_state("hourly_until", end_s)
    return len(hours)


def log_event(device, event_type, value):
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO events (device, ts, type, value)
            VALUES (?, datetime('now'), ?, ?)
        """, (device, event_type, value))


def get_state(key, default="0"):
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_state WHERE key=?", (key,))
        row = c.fetchone()
    return row[0] if row else default


def set_state(key, value):
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO system_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, value))


# esp_reset_reason_t (ESP-IDF, esp_system.h), sent by the nodes as reset_reason (#228)
RESET_REASONS = {
    0: "unknown", 1: "power-on", 2: "external pin", 3: "software", 4: "panic",
    5: "interrupt watchdog", 6: "task watchdog", 7: "watchdog", 8: "deep sleep",
    9: "brownout", 10: "SDIO", 11: "USB", 12: "JTAG", 13: "eFuse error",
    14: "power glitch", 15: "CPU lockup",
}


def reset_reason_text(code):
    return f"{RESET_REASONS.get(code, 'code')} ({code})"


def note_uptime(device, uptime, reset_reason=None):
    """Log a restart when a device's uptime goes backwards, with the node's reset reason."""
    if uptime is None:
        return
    prev = state.LAST_UPTIME.get(device)
    state.LAST_UPTIME[device] = uptime

    # First message since the backend started — no baseline to compare
    if prev is None:
        return

    # A real reboot resets uptime to near zero. Require a meaningful drop, not just
    # a smaller number: a node that publishes on several topics (env, presence) can
    # send them slightly out of order, so its uptime may briefly go back a little.
    if uptime < prev - UPTIME_DROP_TOLERANCE:
        reason = "" if reset_reason is None else f", reason {reset_reason_text(reset_reason)}"
        log_event(device, "RESTART", f"uptime {prev}s -> {uptime}s{reason}")
        log.info("restart %s: was up %ss, now %ss%s", device, prev, uptime, reason)


# -------------------------
# ALERT STATE (survives restarts)
# -------------------------
# Alert state that must survive a backend restart (#5) lives in system_state:
#   cooldown:<key>       = unix time the cooldown for <key> started
#   radar_alerted:<dev>  = '1' while that radar's current outage has been pushed
def load_alert_state():
    with db() as conn:
        rows = conn.execute("""
            SELECT key, value FROM system_state
            WHERE key LIKE 'cooldown:%' OR key LIKE 'radar_alerted:%' OR key LIKE 'node_offline:%'
        """).fetchall()
    cooldowns = {k[len("cooldown:"):]: float(v) for k, v in rows if k.startswith("cooldown:")}
    alerted = {k[len("radar_alerted:"):] for k, v in rows if k.startswith("radar_alerted:") and v == "1"}
    # node_offline: an alerts.Excursion (#305), 'sent' / 'muted' (#82) while offline
    offline = {k[len("node_offline:"):]: v == "sent" for k, v in rows
               if k.startswith("node_offline:") and v in ("sent", "muted")}
    return cooldowns, alerted, offline
