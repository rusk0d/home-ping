"""#82 notification settings: per-kind switches, quiet hours, API, node offline/online pairing.
"""

from support import check


def test_issue82_notification_settings(env):
    import os, sys, json, base64, tempfile, sqlite3, types, time
    from datetime import datetime, timedelta, timezone
    from unittest import mock
    import server
    import config, state, store, alerts, nodes, ingest, api
    server.prepare()   # importing does no work (#73)
    pushes = []
    alerts.send_push = lambda title, body, data=None: pushes.append(data.get("event")) or True
    db = lambda: sqlite3.connect(config.DB)
    last_event = lambda t: (db().execute("SELECT value FROM events WHERE type=? ORDER BY id DESC LIMIT 1", (t,)).fetchone() or [None])[0]
    c = api.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    get = lambda: c.get("/api/notification_settings", headers=A).get_json()
    post = lambda body: c.post("/api/notification_settings", headers=A, json=body)
    mqtt = lambda topic, p: ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic=topic, payload=json.dumps(p).encode()))
    def reset():
        """A fresh excursion: no pushes, no cooldowns, no humidity/temperature excursion flags (#117)."""
        pushes.clear(); state.LAST_NOTIFICATION.clear()
        con = db(); con.execute("DELETE FROM system_state WHERE key LIKE 'env_alerted:%'"); con.commit(); con.close()

    # --- API
    d = get()
    check("defaults: all on, quiet off 22:00-07:00", d == {"humidity": True, "temperature": True, "radar": True, "nodes": True,
                                                           "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"}})
    r = post({"humidity": False, "quiet_hours": {"start": "23:30"}})
    check("partial POST returns full settings", r.status_code == 200 and r.get_json()["humidity"] is False and r.get_json()["quiet_hours"]["start"] == "23:30" and r.get_json()["temperature"] is True)
    check("settings change logged", "notify:humidity=0" in (last_event("NOTIFICATION_SETTINGS") or ""))
    for bad in [{"humidity": "no"}, {"quiet_hours": {"start": "24:00"}}, {"quiet_hours": {"end": "7:00"}}, {"quiet_hours": []},
                {"quiet_hours": {"enabled": 1}}]:
        r = post(bad); check(f"400 for {bad}", r.status_code == 400 and "error" in r.get_json())
    r = post({"temperature": False, "quiet_hours": {"start": "bad"}})
    check("invalid body writes nothing", get()["temperature"] is True)
    check("no auth -> 401", c.get("/api/notification_settings").status_code == 401)
    check("non-object body -> 400", c.post("/api/notification_settings", headers=A, data="[]", content_type="application/json").status_code == 400)

    # --- switches
    reset()
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 15.0, "humidity": 80.0, "uptime": 5})
    check("humidity off: no humidity push, temp push sent", pushes == ["TEMP"])
    check("muted alert logged", (last_event("HUMIDITY_HIGH_ALERT") or "").startswith("muted (off)"))
    check("muted alert sets cooldown", "HUMIDITY_home_ping_esp32" in state.LAST_NOTIFICATION)
    post({"humidity": True}); pushes.clear(); state.LAST_NOTIFICATION.clear()
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 15.0, "humidity": 80.0, "uptime": 6})
    check("humidity back on mid-excursion: the muted excursion stays handled (#117)", pushes == [])
    reset()
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 15.0, "humidity": 80.0, "uptime": 6})
    check("humidity back on: both push", sorted(pushes) == ["HUMIDITY_HIGH", "TEMP"])

    # --- quiet hours (window crossing midnight, and same-day window)
    post({"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}})
    at = lambda hm: datetime(2026, 10, 15, int(hm[:2]), int(hm[3:]))
    check("22:00-07:00: 23:15 quiet, 06:59 quiet, 07:00 not, 21:59 not",
          alerts.in_quiet_hours(at("23:15")) and alerts.in_quiet_hours(at("06:59")) and not alerts.in_quiet_hours(at("07:00")) and not alerts.in_quiet_hours(at("21:59")))
    post({"quiet_hours": {"start": "13:00", "end": "15:00"}})
    check("13:00-15:00: 14:00 quiet, 15:00 and 12:00 not",
          alerts.in_quiet_hours(at("14:00")) and not alerts.in_quiet_hours(at("15:00")) and not alerts.in_quiet_hours(at("12:00")))
    post({"quiet_hours": {"enabled": False}})
    check("disabled: never quiet", not alerts.in_quiet_hours(at("14:00")))

    # Quiet now: a window around the current local time
    now = datetime.now()
    post({"quiet_hours": {"enabled": True, "start": (now - timedelta(hours=1)).strftime("%H:%M"), "end": (now + timedelta(hours=1)).strftime("%H:%M")}})
    reset()
    mqtt("home/esp32/bme280", {"device": "home_ping_esp32", "temp_c": 15.0, "humidity": 80.0, "uptime": 7})
    check("quiet hours: env alerts muted", pushes == [] and (last_event("TEMP_ALERT") or "").startswith("muted (quiet hours)"))

    # Occupancy alert ignores quiet hours
    store.set_state("occupancy_armed", "1")
    db_ = db(); db_.execute("DELETE FROM presence_readings"); db_.commit(); db_.close()
    pres = lambda dev, moving: mqtt("home/esp32/presence", {"device": dev, "radar_connected": True, "presence": moving, "stationary": False,
                                                           "moving": moving, "distance": 100, "signal": 50, "rssi": -60, "uptime": 100})
    pres("home_ping_esp32", False); pres("home_ping_esp32", True)
    check("occupancy alert still pushes during quiet hours", "OCCUPIED" in pushes)

    # Radar alert muted in quiet hours, once per outage
    reset()
    for _ in range(12):
        mqtt("home/esp32/presence", {"device": "hallway_presence_esp32", "radar_connected": False, "presence": False, "uptime": 100})
    check("radar alert muted, outage marked handled", pushes == [] and "hallway_presence_esp32" in state.RADAR_ALERTED
          and (last_event("RADAR_DISCONNECTED") or "").startswith("muted"))

    # --- nodes: muted offline -> no back-online push; sent offline -> back-online push
    state.STARTED_AT = 0
    old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    def make_stale(dev):
        con = db(); con.execute("INSERT OR REPLACE INTO devices (device, last_seen) VALUES (?, ?)", (dev, old)); con.commit(); con.close()
    reset(); make_stale("nodeA"); nodes.check_nodes()
    check("quiet: offline muted, recorded as 'm'", pushes == [] and state.NODE_OFFLINE.get("nodeA") is False and store.get_state("node_offline:nodeA") == "m")
    check("muted state survives restart", store.load_alert_state()[2].get("nodeA") is False)
    post({"quiet_hours": {"enabled": False}})
    nodes.touch_device("nodeA")
    check("back online after muted offline: no push, logged", pushes == [] and "nodeA" not in state.NODE_OFFLINE
          and "muted" in (last_event("NODE_ONLINE") or ""))
    reset(); make_stale("nodeB"); nodes.check_nodes()
    check("offline push sent, recorded as '1'", pushes == ["NODE_STATUS"] and store.get_state("node_offline:nodeB") == "1")
    nodes.touch_device("nodeB")
    check("back online push sent", pushes == ["NODE_STATUS", "NODE_STATUS"] and store.get_state("node_offline:nodeB") == "0")
    post({"nodes": False}); reset(); state.LAST_NOTIFICATION.clear(); make_stale("nodeC"); nodes.check_nodes()
    check("nodes off: offline muted (off)", pushes == [] and (last_event("NODE_OFFLINE") or "").startswith("muted (off)"))
    # legacy '1' value still loads as pushed
    store.set_state("node_offline:legacy", "1")
    check("legacy node_offline '1' loads as pushed", store.load_alert_state()[2].get("legacy") is True)
