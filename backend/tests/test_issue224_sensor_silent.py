"""#224: push when a sensor on a live node goes silent (BME280, MH-Z19C), and when it's back.
"""

from support import check


def test_issue224_sensor_silent(env):
    import sqlite3
    import server
    import config, state, store, alerts, nodes
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [(c.kwargs["data"]["device"], c.kwargs["data"]["status"]) for c in m.Message.call_args_list
               if c.kwargs.get("data", {}).get("event") == "SENSOR_STATUS"]
        m.Message.reset_mock()
        return out

    def sql(q, *args):
        conn = sqlite3.connect(config.DB)
        conn.execute(q, args)
        conn.commit()

    def ago(s):
        return f"-{s} seconds"

    def presence(device, s):   # a radar row s seconds ago, plus the heartbeat
        sql("INSERT INTO presence_readings (device, ts, occupied) VALUES (?, datetime('now', ?), 0)", device, ago(s))
        sql("INSERT INTO devices (device, last_seen) VALUES (?, datetime('now', ?)) "
            "ON CONFLICT(device) DO UPDATE SET last_seen = MAX(last_seen, excluded.last_seen)", device, ago(s))

    def env_row(device, s):
        sql("INSERT INTO readings (device, ts, temp, humidity) VALUES (?, datetime('now', ?), 21, 50)", device, ago(s))

    def co2_row(device, s, ok=True):
        sql("INSERT INTO readings (device, ts, co2, co2_ok) VALUES (?, datetime('now', ?), ?, ?)",
            device, ago(s), 600 if ok else None, 1 if ok else 0)

    main, hall = "home_ping_esp32", "hallway_presence_esp32"
    silent = config.SENSOR_SILENT_SECONDS

    # Both sensors healthy, the radar node has neither: nothing
    for s in range(0, 3600, 60):
        presence(main, s); presence(hall, s); env_row(main, s); co2_row(main, s)
    nodes.check_sensors()
    check("healthy: no push", pushes() == [])
    check("radar-only node: no sensor states",
          store.get_state(f"sensor_silent:{hall}:env", None) is None and store.get_state(f"sensor_silent:{hall}:co2", None) is None)

    # BME280 dead for 20 min (no env rows), CO2 dead too (co2_ok false), radar keeps going
    sql("DELETE FROM readings WHERE ts >= datetime('now', ?)", ago(20 * 60))
    for s in range(0, 20 * 60, 60):
        co2_row(main, s, ok=False)
    nodes.check_sensors()
    check("both silent: one push each", sorted(pushes()) == [(f"{main}:co2", "silent"), (f"{main}:env", "silent")])
    nodes.check_sensors()
    check("once per excursion", pushes() == [])

    # Values back
    env_row(main, 0); co2_row(main, 0)
    nodes.check_sensors()
    check("back: one push each", sorted(pushes()) == [(f"{main}:co2", "ok"), (f"{main}:env", "ok")])
    check("state cleared", store.get_state(f"sensor_silent:{main}:env") == "0")

    # Node offline: its sensors don't alert on top of NODE_STATUS
    sql("DELETE FROM readings"); sql("DELETE FROM presence_readings"); sql("DELETE FROM devices")
    for s in range(silent + 600, silent + 3600, 60):
        presence(main, s); env_row(main, s); co2_row(main, s)
    nodes.check_sensors()
    check("node offline: no sensor push", pushes() == [])

    # Node just came back after an outage longer than the window (radar first, env not yet)
    presence(main, 10)
    nodes.check_sensors()
    check("back from an outage, sensors not yet reported: no push", pushes() == [])

    # Main node in NODE_OFFLINE (pushed, not yet back) is skipped too
    sql("DELETE FROM readings"); sql("DELETE FROM presence_readings"); sql("DELETE FROM devices")
    for s in range(0, 3600, 60):
        presence(main, s)
        if s > silent + 60:
            env_row(main, s)
    state.NODE_OFFLINE[main] = True
    nodes.check_sensors()
    check("NODE_OFFLINE device: skipped", pushes() == [])
    state.NODE_OFFLINE.pop(main)

    # Quiet hours postpone the push; the switch off mutes it (no "back" push later)
    store.set_state("quiet_hours", "1"); store.set_state("quiet_start", "00:00"); store.set_state("quiet_end", "23:59")
    nodes.check_sensors()
    check("quiet hours: postponed", pushes() == [] and store.get_state(f"sensor_silent:{main}:env", "0") == "0")
    store.set_state("quiet_hours", "0")
    store.set_state("notify:nodes", "0")
    nodes.check_sensors()
    check("switch off: muted, no push", pushes() == [] and store.get_state(f"sensor_silent:{main}:env") == "m")
    store.set_state("notify:nodes", "1")
    env_row(main, 0)
    nodes.check_sensors()
    check("back after a muted warning: no push", pushes() == [] and store.get_state(f"sensor_silent:{main}:env") == "0")
