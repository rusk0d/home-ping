"""#173: raw readings are kept RAW_RETENTION_DAYS (2); the hourly table keeps the history.
"""

from support import check


def test_issue173_raw_retention(env):
    import sqlite3
    import server, config, store
    server.prepare()
    con = sqlite3.connect(config.DB)
    for table, cols in (("readings", "device, ts, temp"), ("presence_readings", "device, ts, occupied")):
        for age, v in (("-49 hours", 1), ("-47 hours", 2)):
            con.execute(f"INSERT INTO {table} ({cols}) VALUES ('home_ping_esp32', datetime('now',?), ?)", (age, v))
    con.commit()
    con.execute("DELETE FROM system_state WHERE key = 'hourly_until'"); con.commit()
    store.rollup_hourly()
    check("rollup saw both rows before the prune", con.execute("SELECT COUNT(*) FROM hourly").fetchone()[0] >= 1)

    store.log_to_db("home_ping_esp32", 21.0, 45.0, 100)
    store.log_presence("home_ping_esp32", 1, 1, 0, 0, 100, 50, -60, 100, 1)
    check("readings: > 2 days old pruned, < 2 days kept",
          sorted(r[0] for r in con.execute("SELECT temp FROM readings")) == [2, 21.0])
    check("presence_readings: same", con.execute("SELECT COUNT(*) FROM presence_readings").fetchone()[0] == 2)
    check("hourly is never pruned", con.execute("SELECT COUNT(*) FROM hourly").fetchone()[0] >= 1)
    con.close()
