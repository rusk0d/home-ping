"""#306: init_db() runs numbered migrations once each (system_state schema_version); a
database from before that starts from what its old one-off flags show."""
import sqlite3

from support import check


def test_issue306_migrations(env):
    import config, store

    def q(sql, *args):
        with store.db() as conn:
            return conn.execute(sql, args).fetchall()

    # --- a new database: every migration, then the version
    store.init_db()
    check("new: at the newest version", store.get_state("schema_version", None) == str(store.SCHEMA_VERSION))
    check("no old flags", q("SELECT key FROM system_state WHERE key IN ('ts_utc', 'hourly_room_rebuilt')") == [])

    # --- the live database's case: from before schema_version, #65 and #290 done
    with store.db() as conn:
        conn.execute("DELETE FROM system_state WHERE key = 'schema_version'")
        conn.execute("INSERT INTO system_state VALUES ('ts_utc', '1'), ('hourly_room_rebuilt', '1')")
        conn.execute("INSERT INTO readings (device, ts, temp) VALUES ('a', '2026-11-07 10:00:00', 20)")
        conn.execute("INSERT INTO system_state VALUES ('watchdog:disk', 'm')")
    store.init_db()
    check("UTC conversion not run again", q("SELECT ts FROM readings") == [("2026-11-07 10:00:00",)])
    check("#305 conversion run", store.get_state("watchdog:disk") == "muted")
    check("old flags removed, version set",
          q("SELECT key FROM system_state WHERE key IN ('ts_utc', 'hourly_room_rebuilt')") == []
          and store.get_state("schema_version") == str(store.SCHEMA_VERSION))

    # --- only the ones not run yet; each one once
    ran = []
    saved = store.MIGRATIONS
    store.MIGRATIONS = saved + ((store.SCHEMA_VERSION + 1, lambda c: ran.append(1)),)
    store.init_db()
    store.init_db()
    store.MIGRATIONS = saved
    check("a new migration runs once", ran == [1] and store.get_state("schema_version") == str(store.SCHEMA_VERSION + 1))

    # --- the #14 rebuild doesn't lose the readings indexes
    con = sqlite3.connect(config.DB)
    con.execute("DROP TABLE readings")
    con.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, ts TIMESTAMP, temp REAL, "
                "humidity REAL, uptime INTEGER, occupied INTEGER)")
    con.execute("UPDATE system_state SET value = '0' WHERE key = 'schema_version'")
    con.execute("DELETE FROM events")   # the UTC conversion would shift them; nothing to check there
    con.commit(); con.close()
    store.init_db()
    cols = {r[1] for r in q("PRAGMA table_info(readings)")}
    idx = {r[1] for r in q("PRAGMA index_list(readings)")}
    check("rebuilt without the old columns", "occupied" not in cols and "alert" in cols)
    check(f"indexes there after the rebuild ({idx})", {"ix_readings_device_id", "ix_readings_device_ts"} <= idx)
