"""#296: /api/notification_history is the last 20 pushes that went out, newest first."""
import base64

from support import check


def test_issue296_notification_history(env):
    import server, api
    from store import log_event
    server.prepare()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    get = lambda: api.app.test_client().get("/api/notification_history", headers=auth).get_json()["events"]

    check("nothing yet", get() == [])

    log_event("home_ping_esp32", "HUMIDITY_HIGH_ALERT", "sent, humidity=70")
    log_event("home_ping_esp32", "TEMP_ALERT", "muted (quiet hours), temp=17")
    log_event("home_ping_esp32", "TEMP_ALERT", "failed, temp=17")
    for _ in range(30):   # a busy hour of radar flips must not push the pushes out
        log_event("home_ping_esp32", "OCCUPANCY_CHANGE", "occupied")
    log_event("fridge_temp_esp32", "FRIDGE_ALERT", "sent, alert=warm_low (was: normal)")

    events = get()
    check("only sent pushes", [e["type"] for e in events] == ["FRIDGE_ALERT", "HUMIDITY_HIGH_ALERT"])
    check("detail without the status", events[1]["detail"] == "humidity=70" and events[1]["device"] == "home_ping_esp32")
    check("ISO timestamp", events[0]["ts"].endswith("Z") and "T" in events[0]["ts"])

    for i in range(25):
        log_event("home_ping_esp32", "NODE_OFFLINE", f"sent, n={i}")
    events = get()
    check("at most 20, newest first", len(events) == 20 and events[0]["detail"] == "n=24")
