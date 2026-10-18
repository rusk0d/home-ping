"""#227: occupancy_stats takes all three values from the same radar.
"""

from support import check


def test_issue227_occupancy_coverage(env):
    import sqlite3
    import server
    import config, store
    server.prepare()

    def radar(device, hours_ago_from, hours_ago_to, occupied):
        # one row every 6 min over the span, all occupied or all not
        conn = sqlite3.connect(config.DB)
        n = int((hours_ago_from - hours_ago_to) * 10) + 1
        for i in range(n):
            h = hours_ago_from - i * (hours_ago_from - hours_ago_to) / (n - 1)
            conn.execute("INSERT INTO presence_readings (device, ts, occupied) "
                         "VALUES (?, datetime('now', ?), ?)", (device, f"-{h * 3600:.0f} seconds", occupied))
        conn.commit()

    # Main radar: 20 h of data, nobody there. Hallway: only the last 2 h, occupied all along.
    radar("home_ping_esp32", 20, 0, 0)
    radar("hallway_presence_esp32", 2, 0, 1)
    s = store.occupancy_stats(24)
    check("winner is the hallway radar (100 %)", s["avg_occupancy"] == 100)
    check("occupied_hours from its 2 h span", s["occupied_hours"] == 2.0)
    check("coverage_hours from the same radar, not the 20 h one", s["coverage_hours"] == 2.0)

    # Nobody anywhere: equal 0 %, the longer span answers (coverage isn't lost)
    sqlite3.connect(config.DB).execute("DELETE FROM presence_readings").connection.commit()
    radar("home_ping_esp32", 20, 0, 0)
    radar("hallway_presence_esp32", 2, 0, 0)
    s = store.occupancy_stats(24)
    check("0 % on both: coverage from the longer span", s == {"avg_occupancy": 0, "occupied_hours": 0.0, "coverage_hours": 20.0})

    sqlite3.connect(config.DB).execute("DELETE FROM presence_readings").connection.commit()
    check("no data: all null", store.occupancy_stats(24) == {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None})
