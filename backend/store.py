"""SQLite: the db() helper, schema/migrations and the read/write helpers.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config
import state
from config import (CONFIDENCE_MINUTES, DB, EVENTS_RETENTION_DAYS, PRESENCE_DEVICE, RAW_RETENTION_DAYS,
                    UPTIME_DROP_TOLERANCE)

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
    """A connection that is committed on success and always closed (#73)."""
    conn = sqlite3.connect(config.DB)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# -------------------------
# DATABASE INIT
# -------------------------
READINGS_COLUMNS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    ts TIMESTAMP,
    temp REAL,
    humidity REAL,
    uptime INTEGER,
    co2 INTEGER,
    pressure REAL,
    co2_ok INTEGER
"""


def init_db():
    with db() as conn:
        c = conn.cursor()

        c.execute(f"CREATE TABLE IF NOT EXISTS readings ({READINGS_COLUMNS})")

        # Add columns missing from older databases (including ones created by the
        # removed init_db.py, which had only id/ts/temp/humidity)
        for col, coltype in (("device", "TEXT"), ("uptime", "INTEGER"),
                             ("co2", "INTEGER"), ("pressure", "REAL"), ("co2_ok", "INTEGER")):
            try:
                c.execute(f"ALTER TABLE readings ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Drop columns that were always 0 (#14). Rebuild the table instead of
        # ALTER TABLE ... DROP COLUMN, which needs SQLite 3.35+.
        cols = {r[1] for r in c.execute("PRAGMA table_info(readings)")}
        if cols & {"occupied", "occupancy_confidence", "dark"}:
            keep = "id, device, ts, temp, humidity, uptime, co2, pressure, co2_ok"
            conn.commit()
            c.executescript(f"""
                BEGIN;
                CREATE TABLE readings_new ({READINGS_COLUMNS});
                INSERT INTO readings_new ({keep}) SELECT {keep} FROM readings;
                DROP TABLE readings;
                ALTER TABLE readings_new RENAME TO readings;
                COMMIT;
            """)
            log.info("readings: dropped occupied, occupancy_confidence, dark")

        c.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device TEXT PRIMARY KEY,
                last_seen TIMESTAMP
            )
        """)
        # register_token used to add phones as "unknown"; devices is for sensor nodes only
        c.execute("DELETE FROM devices WHERE device = 'unknown'")

        c.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT,
                ts TIMESTAMP,
                type TEXT,
                value TEXT
            )
        """)

        c.execute("""
            INSERT OR IGNORE INTO system_state (key, value)
            VALUES ('occupancy_armed', '0')
        """)
        # Soil moisture feature removed (#9): no sensor exists, drop its unused table
        c.execute("DROP TABLE IF EXISTS soil_readings")

        # Presence readings from the LD2410C radar node
        c.execute("""
            CREATE TABLE IF NOT EXISTS presence_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT,
                ts TIMESTAMP,
                occupied INTEGER,
                presence INTEGER,
                stationary INTEGER,
                moving INTEGER,
                distance INTEGER,
                signal INTEGER,
                rssi INTEGER,
                uptime INTEGER,
                radar_connected INTEGER
            )
        """)

        # Long-term history (#129): one row per complete hour, all devices combined (like
        # the app's charts), filled by rollup_hourly() from the raw tables, which are only
        # kept RAW_RETENTION_DAYS (2, #173). Kept for good: ~2.4 KB a day.
        c.execute("""
            CREATE TABLE IF NOT EXISTS hourly (
                hour TEXT PRIMARY KEY,   -- start of the hour, UTC, "YYYY-MM-DD HH:00:00"
                temp_avg REAL, temp_min REAL, temp_max REAL,
                humidity_avg REAL, humidity_min REAL, humidity_max REAL,
                co2_avg REAL, co2_min INTEGER, co2_max INTEGER,
                pressure_avg REAL, pressure_min REAL, pressure_max REAL,
                occupancy REAL           -- % of PRESENCE_DEVICE's radar rows occupied
            )
        """)

        # Timestamps were stored in server local time until #65; convert existing rows to
        # UTC once. SQLite's 'utc' modifier applies the DST offset of each row's own date.
        if c.execute("SELECT 1 FROM system_state WHERE key = 'ts_utc'").fetchone() is None:
            for table, col in (("readings", "ts"), ("presence_readings", "ts"),
                               ("events", "ts"), ("devices", "last_seen")):
                c.execute(f"UPDATE {table} SET {col} = datetime({col}, 'utc') WHERE {col} IS NOT NULL")
            c.execute("INSERT INTO system_state (key, value) VALUES ('ts_utc', '1')")
            conn.commit()
            log.info("timestamps converted to UTC")

        # Indexes for the per-device lookups (#18). (device, id) serves "latest row of a
        # device"; (device, ts) serves time windows. No plain ts index: it made SQLite pick
        # worse plans for several endpoints (measured on an 8-day, 276k-row database).
        for sql in (
            "CREATE INDEX IF NOT EXISTS ix_presence_device_id ON presence_readings(device, id)",
            "CREATE INDEX IF NOT EXISTS ix_presence_device_ts ON presence_readings(device, ts)",
            "CREATE INDEX IF NOT EXISTS ix_readings_device_id ON readings(device, id)",
            "CREATE INDEX IF NOT EXISTS ix_readings_device_ts ON readings(device, ts)",
            "CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts)",
        ):
            c.execute(sql)
        conn.commit()
        # Without statistics SQLite picks the wrong index for several queries
        c.execute("ANALYZE")


def log_to_db(device, temp, hum, uptime, co2=None, pressure=None, co2_ok=None):
    with db() as conn:
        c = conn.cursor()

        c.execute("""
            INSERT INTO readings
            (device, ts, temp, humidity, uptime, co2, pressure, co2_ok)
            VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)
        """, (device, temp, hum, uptime, co2, pressure, co2_ok))

        c.execute("DELETE FROM readings WHERE ts < datetime('now',?)",
                  (f"-{RAW_RETENTION_DAYS} days",))


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

        # RAW_RETENTION_DAYS, so the 24h queries always have full coverage (#173)
        c.execute("DELETE FROM presence_readings WHERE ts < datetime('now',?)",
                  (f"-{RAW_RETENTION_DAYS} days",))


HOURLY_COLUMNS = ("hour", "temp_avg", "temp_min", "temp_max", "humidity_avg", "humidity_min",
                  "humidity_max", "co2_avg", "co2_min", "co2_max", "pressure_avg", "pressure_min",
                  "pressure_max", "occupancy")


def rollup_hourly(now=None):
    """Aggregate complete hours from readings/presence_readings into `hourly` (#129).

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
        for row in conn.execute("""
            SELECT strftime('%Y-%m-%d %H:00:00', ts),
                   AVG(temp), MIN(temp), MAX(temp), AVG(humidity), MIN(humidity), MAX(humidity),
                   AVG(co2), MIN(co2), MAX(co2), AVG(pressure), MIN(pressure), MAX(pressure)
            FROM readings WHERE ts >= ? AND ts < ? GROUP BY 1
        """, (since, end_s)):
            hours[row[0]] = list(row) + [None]
        for h, occ in conn.execute("""
            SELECT strftime('%Y-%m-%d %H:00:00', ts), AVG(occupied) * 100
            FROM presence_readings WHERE device = ? AND ts >= ? AND ts < ? GROUP BY 1
        """, (PRESENCE_DEVICE, since, end_s)):
            hours.setdefault(h, [h] + [None] * 12 + [None])[13] = occ
        conn.executemany(
            f"INSERT OR REPLACE INTO hourly ({', '.join(HOURLY_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(HOURLY_COLUMNS))})",
            hours.values())
    set_state("hourly_until", end_s)
    return len(hours)


def occupancy_stats(hours=24):
    """Occupancy over a window, using each radar's real sample spacing."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT device,
                   COUNT(*),
                   SUM(occupied),
                   MIN(ts),
                   MAX(ts)
            FROM presence_readings
            WHERE ts >= datetime('now', ?)
            GROUP BY device
        """, (f"-{hours} hours",))
        rows = c.fetchall()

    if not rows:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}

    best_pct = 0
    best_hours = 0
    coverage = 0

    for device, total, occupied, first_ts, last_ts in rows:
        if not total:
            continue
        pct = (occupied / total) * 100

        # Real span this device actually covered
        t0 = parse_ts(first_ts)
        t1 = parse_ts(last_ts)
        span_h = max((t1 - t0).total_seconds() / 3600, 0)

        if pct > best_pct:
            best_pct = pct
            best_hours = span_h * (occupied / total)
        coverage = max(coverage, span_h)

    return {
        "avg_occupancy": round(best_pct, 0),
        "occupied_hours": round(best_hours, 2),
        "coverage_hours": round(coverage, 1),
    }


def calculate_confidence(device, minutes=CONFIDENCE_MINUTES):
    """Occupancy confidence for one radar over a time window (0-100), None without data."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ? AND ts >= datetime('now', ?)
        """, (device, f"-{minutes} minutes"))
        rows = c.fetchall()

    if not rows:
        return None

    return int((sum(r[0] for r in rows) / len(rows)) * 100)


def get_combined_occupancy():
    """1 if any radar has 2+ occupied rows in its last 3 reports (last 2 min), 0 if not,
    None if no radar reported in the last 2 minutes."""
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT device FROM presence_readings")
        devices = [r[0] for r in c.fetchall()]

        result = None
        for d in devices:
            c.execute("""
                SELECT occupied FROM presence_readings
                WHERE device = ? AND ts >= datetime('now','-2 minutes')
                ORDER BY id DESC LIMIT 3
            """, (d,))
            rows = [r[0] for r in c.fetchall()]
            if rows and result is None:
                result = 0
            if sum(rows) >= 2:
                result = 1
    return result


def calculate_combined_confidence(minutes=CONFIDENCE_MINUTES):
    """Per-radar confidence over the window, then the highest of them; None without data."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT device, AVG(occupied)
            FROM presence_readings
            WHERE ts >= datetime('now', ?)
            GROUP BY device
        """, (f"-{minutes} minutes",))
        rows = c.fetchall()

    if not rows:
        return None
    return int(max(r[1] for r in rows) * 100)


def get_last_occupancy(device):
    """Previous (second most recent) radar occupancy state."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ?
            ORDER BY id DESC LIMIT 1 OFFSET 1
        """, (device,))
        row = c.fetchone()
    return row[0] if row else None


def log_event(device, event_type, value):
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO events (device, ts, type, value)
            VALUES (?, datetime('now'), ?, ?)
        """, (device, event_type, value))
        c.execute("DELETE FROM events WHERE ts < datetime('now',?)",
                  (f"-{EVENTS_RETENTION_DAYS} days",))


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


def note_uptime(device, uptime):
    """Log a restart when a device's uptime goes backwards."""
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
        log_event(device, "RESTART", f"uptime {prev}s -> {uptime}s")
        log.info("restart %s: was up %ss, now %ss", device, prev, uptime)


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
    # node_offline: '1' = offline push sent, 'm' = offline but muted (#82), '0' = online
    offline = {k[len("node_offline:"):]: v == "1" for k, v in rows
               if k.startswith("node_offline:") and v in ("1", "m")}
    return cooldowns, alerted, offline
