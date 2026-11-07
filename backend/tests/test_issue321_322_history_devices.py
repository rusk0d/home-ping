"""#321: /api/notification_history?muted=1 also lists muted alerts; #322: /api/devices has
each node's health (RSSI, uptime, heap, reset reason), the fresh values /api/metrics has."""
import base64
import time

from support import check


def test_issue321_322_history_devices(env):
    import server, api, state, store, metrics
    server.prepare()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    get = lambda path: api.app.test_client().get(path, headers=auth).get_json()

    # --- #321: sent by default, muted on request; failed and "no push" never
    store.log_event("dev", "HUMIDITY_HIGH_ALERT", "sent, humidity=70")
    store.log_event("dev", "TEMP_ALERT", "muted (quiet hours), temp=17")
    store.log_event("dev", "NODE_OFFLINE", "failed, last seen x")
    store.log_event("dev", "NODE_ONLINE", "no push (alert was muted), data again")
    store.log_event("server", "WATCHDOG_DISK", "muted (off), Only 100 MB free")
    plain = get("/api/notification_history")["events"]
    check("default: only sent, status sent",
          [(e["type"], e["status"], e["muted_reason"]) for e in plain] == [("HUMIDITY_HIGH_ALERT", "sent", None)])
    both = get("/api/notification_history?muted=1")["events"]
    check(f"muted=1: newest first, with the reason ({both})", [(e["type"], e["status"], e["muted_reason"], e["detail"]) for e in both] == [
        ("WATCHDOG_DISK", "muted", "off", "Only 100 MB free"),
        ("TEMP_ALERT", "muted", "quiet hours", "temp=17"),
        ("HUMIDITY_HIGH_ALERT", "sent", None, "humidity=70")])

    # --- #322: health in /api/devices, fresh values only
    with store.db() as conn:
        conn.execute("INSERT INTO devices (device, last_seen) VALUES ('home_ping_esp32', datetime('now'))")
        conn.execute("INSERT INTO devices (device, last_seen) VALUES ('fridge_temp_esp32', datetime('now'))")
    metrics.note_node("home_ping_esp32", rssi=-61, uptime=3600, heap_free=150000, heap_min=120000,
                      heap_block=100000, reset_reason=9, radar_connected=1, occupied=1)
    metrics.note_node("fridge_temp_esp32", rssi=-70, uptime=60)
    state.NODE_HEALTH["fridge_temp_esp32"]["rssi"] = (-70, time.time() - 10_000)   # stale
    devs = {d["device"]: d["health"] for d in get("/api/devices")}
    check(f"main node health ({devs['home_ping_esp32']})", devs["home_ping_esp32"] == {
        "rssi": -61, "uptime": 3600, "heap_free": 150000, "heap_min": 120000, "heap_block": 100000,
        "reset_reason": "brownout", "radar_connected": True})
    check(f"stale values left out ({devs['fridge_temp_esp32']})", devs["fridge_temp_esp32"] == {"uptime": 60})
    check("/api/metrics still has them", "homeping_node_wifi_rssi_dbm{device=\"home_ping_esp32\"} -61" in metrics.render())
