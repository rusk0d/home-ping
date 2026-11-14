"""The database schema (#425, from store.py): init_db() on every start creates or extends the
tables, columns and indexes (idempotent), then runs the numbered MIGRATIONS not yet run on this
database (system_state schema_version, #306). contracts/db-schema.md.
"""

import logging
import sqlite3

from config import FRIDGE_DEVICE, NON_ROOM_DEVICES, ROOM_DEVICES_SQL
from store import _HOUR_SQL, db

log = logging.getLogger(__name__)


READINGS_COLUMNS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    ts TIMESTAMP,
    temp REAL,
    humidity REAL,
    uptime INTEGER,
    co2 INTEGER,
    pressure REAL,
    co2_ok INTEGER,
    alert TEXT
"""


def init_db():
    """The schema on every start (idempotent: tables, columns, indexes), then the numbered
    MIGRATIONS that haven't run on this database yet (#306)."""
    with db() as conn:
        # Persistent in the file: once set, every connection uses WAL (#174). Next to
        # data.db, SQLite keeps data.db-wal and data.db-shm while connections are open.
        # Switching needs a moment without other connections (e.g. a backup running):
        # if that fails, the backend runs on in the old mode and tries again next start.
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                log.warning("journal_mode is %s, not wal", mode)
        except sqlite3.OperationalError as e:
            log.warning("couldn't switch to WAL: %s", e)
        _schema(conn)
        conn.commit()
        _migrate(conn)
        _indexes(conn)   # after the migrations: a table rebuild (#14) drops its indexes
        conn.commit()
        # Without statistics SQLite picks the wrong index for several queries
        conn.execute("ANALYZE")


def _schema(conn):
    """What the tables look like now. Safe to run on every start."""
    c = conn.cursor()
    c.execute(f"CREATE TABLE IF NOT EXISTS readings ({READINGS_COLUMNS})")

    # Add columns missing from older databases (including ones created by the
    # removed init_db.py, which had only id/ts/temp/humidity)
    for col, coltype in (("device", "TEXT"), ("uptime", "INTEGER"),
                         ("co2", "INTEGER"), ("pressure", "REAL"), ("co2_ok", "INTEGER"),
                         ("alert", "TEXT")):
        try:
            c.execute(f"ALTER TABLE readings ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # Column already exists

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

    # Long-term history (#129): one row per complete hour, the room devices combined (like
    # the app's charts) and the fridge on its own (#331), filled by rollup_hourly() from the
    # raw tables, which are only kept RAW_RETENTION_DAYS (2, #173). Kept for good: ~3 KB a day.
    c.execute("""
        CREATE TABLE IF NOT EXISTS hourly (
            hour TEXT PRIMARY KEY,   -- start of the hour, UTC, "YYYY-MM-DD HH:00:00"
            temp_avg REAL, temp_min REAL, temp_max REAL,
            humidity_avg REAL, humidity_min REAL, humidity_max REAL,
            co2_avg REAL, co2_min INTEGER, co2_max INTEGER,
            pressure_avg REAL, pressure_min REAL, pressure_max REAL,
            occupancy REAL,          -- % of PRESENCE_DEVICE's radar rows occupied
            fridge_avg REAL, fridge_min REAL, fridge_max REAL   -- FRIDGE_DEVICE's temp, °C (#331)
        )
    """)
    for col in ("fridge_avg", "fridge_min", "fridge_max"):   # tables from before #331
        try:
            c.execute(f"ALTER TABLE hourly ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass   # already there


def _indexes(conn):
    # Indexes for the per-device lookups (#18). (device, id) serves "latest row of a
    # device"; (device, ts) serves time windows. No plain ts index: it made SQLite pick
    # worse plans for several endpoints (measured on an 8-day, 276k-row database).
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_presence_device_id ON presence_readings(device, id)",
        "CREATE INDEX IF NOT EXISTS ix_presence_device_ts ON presence_readings(device, ts)",
        "CREATE INDEX IF NOT EXISTS ix_readings_device_id ON readings(device, id)",
        "CREATE INDEX IF NOT EXISTS ix_readings_device_ts ON readings(device, ts)",
        "CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts)",
        # The newest occupied radar row (any_radar_quiet_minutes, every presence message): an
        # index seek instead of scanning back through hours of vacant rows (#304)
        "CREATE INDEX IF NOT EXISTS ix_presence_occupied_id ON presence_readings(id) WHERE occupied = 1",
    ):
        conn.execute(sql)




def _migrate_drop_zero_columns(c):
    """#14: drop the readings columns that were always 0. Rebuilds the table instead of
    ALTER TABLE ... DROP COLUMN, which needs SQLite 3.35+."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(readings)")}
    if cols & {"occupied", "occupancy_confidence", "dark"}:
        keep = "id, device, ts, temp, humidity, uptime, co2, pressure, co2_ok"
        c.connection.commit()
        c.executescript(f"""
            BEGIN;
            CREATE TABLE readings_new ({READINGS_COLUMNS});
            INSERT INTO readings_new ({keep}) SELECT {keep} FROM readings;
            DROP TABLE readings;
            ALTER TABLE readings_new RENAME TO readings;
            COMMIT;
        """)
        log.info("readings: dropped occupied, occupancy_confidence, dark")


def _migrate_utc(c):
    """#65: timestamps were stored in server local time; convert them to UTC. SQLite's 'utc'
    modifier applies the DST offset of each row's own date. Must never run twice."""
    for table, col in (("readings", "ts"), ("presence_readings", "ts"),
                       ("events", "ts"), ("devices", "last_seen")):
        c.execute(f"UPDATE {table} SET {col} = datetime({col}, 'utc') WHERE {col} IS NOT NULL")
    log.info("timestamps converted to UTC")


def _migrate_hourly_without_fridge(c):
    """#290: the hourly roll-up averaged the fridge in with the room. Rewind it to the
    fridge's first reading so the next rollup_hourly() rebuilds those hours without it,
    and clear the room temperature alert it latched as "low"."""
    first = c.execute(f"SELECT MIN(ts) FROM readings WHERE NOT {ROOM_DEVICES_SQL}").fetchone()[0]
    until = c.execute("SELECT value FROM system_state WHERE key = 'hourly_until'").fetchone()
    if first and until and first[:13] + ":00:00" < until[0]:
        c.execute("UPDATE system_state SET value = ? WHERE key = 'hourly_until'", (first[:13] + ":00:00",))
    c.execute(f"DELETE FROM system_state WHERE key IN ({', '.join('?' * len(NON_ROOM_DEVICES))})",
              [f"env_alerted:TEMP_{d}" for d in NON_ROOM_DEVICES])


def _migrate_excursion_format(c):
    """#305: the excursion states in the alerts.Excursion format: '1' / 'm' / '0' became
    'sent' / 'muted' / '', a bare humidity/temperature/pressure band 'sent:<band>' (they had
    no recovery push, so muted or sent didn't matter)."""
    c.execute("""
        UPDATE system_state SET value = CASE value WHEN '1' THEN 'sent' WHEN 'm' THEN 'muted' ELSE '' END
        WHERE (key LIKE 'watchdog:%' OR key LIKE 'sensor_silent:%' OR key LIKE 'node_offline:%')
          AND value IN ('1', 'm', '0')""")
    c.execute("""
        UPDATE system_state SET value = 'sent:' || value
        WHERE key LIKE 'env_alerted:%' AND value IN ('high', 'low', 'falling')""")


def _migrate_fridge_hourly(c):
    """#331: the fridge columns of `hourly` for the hours already rolled up, from the raw
    readings still kept (RAW_RETENTION_DAYS); older fridge history doesn't exist. Only the
    fridge columns: the room ones of those hours stay as they are."""
    until = c.execute("SELECT value FROM system_state WHERE key = 'hourly_until'").fetchone()
    if until is None:
        return   # never rolled up: the first rollup_hourly() does every hour
    c.execute(f"""
        INSERT INTO hourly (hour, fridge_avg, fridge_min, fridge_max)
        SELECT {_HOUR_SQL}, AVG(temp), MIN(temp), MAX(temp) FROM readings
        WHERE device = ? AND temp IS NOT NULL AND ts < ? GROUP BY 1
        ON CONFLICT(hour) DO UPDATE SET fridge_avg = excluded.fridge_avg,
            fridge_min = excluded.fridge_min, fridge_max = excluded.fridge_max
    """, (FRIDGE_DEVICE, until[0]))


# One-off data changes, in order; each runs once per database and is recorded as
# system_state schema_version = its number. Append only: never renumber or remove one.
MIGRATIONS = (
    (1, _migrate_drop_zero_columns),       # #14
    (2, _migrate_utc),                     # #65
    (3, _migrate_hourly_without_fridge),   # #290
    (4, _migrate_excursion_format),        # #305
    (5, _migrate_fridge_hourly),           # #331
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _legacy_version(c):
    """A database from before schema_version (#306): how far its old one-off flags show it
    got. A new database has none of them and runs every migration (no rows to change)."""
    flags = {k for (k,) in c.execute(
        "SELECT key FROM system_state WHERE key IN ('ts_utc', 'hourly_room_rebuilt')")}
    if "hourly_room_rebuilt" in flags:
        return 3
    if "ts_utc" in flags:
        return 2   # #14 ran on every start before #65 existed
    return 0


def _migrate(conn):
    c = conn.cursor()
    row = c.execute("SELECT value FROM system_state WHERE key = 'schema_version'").fetchone()
    if row is None:
        version = _legacy_version(c)
        c.execute("INSERT INTO system_state (key, value) VALUES ('schema_version', ?)", (str(version),))
        c.execute("DELETE FROM system_state WHERE key IN ('ts_utc', 'hourly_room_rebuilt')")
        conn.commit()
    else:
        version = int(row[0])
    for number, migration in MIGRATIONS:
        if number <= version:
            continue
        migration(c)
        c.execute("UPDATE system_state SET value = ? WHERE key = 'schema_version'", (str(number),))
        conn.commit()   # with its version: a crash re-runs only the unfinished one
        log.info("migration %d (%s) done", number, migration.__name__)
