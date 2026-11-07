"""#305: one helper for once-per-excursion alerts (alerts.Excursion), and the one-time
conversion of the old state values."""
from support import check


def test_issue305_excursion(env):
    import server
    import alerts, state, store
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushes, fail = [], []
    def send(msg):
        if fail:
            raise RuntimeError("FCM down")
        pushes.append((msg["data"]["title"], msg["data"].get("band", "")))
        return "id"
    m.send.side_effect = send

    def events(event_type):
        with store.db() as conn:
            return [r[0] for r in conn.execute(
                "SELECT value FROM events WHERE type = ? ORDER BY id", (event_type,))]

    ex = alerts.Excursion("test:x", "nodes", "dev", {"event": "X", "device": "dev"})

    # --- once per excursion, the end replaces it
    check("start pushes", ex.start("X_ON", "d1", "On", "b") == "sent" and pushes == [("On", "")])
    check("saved as sent", store.get_state("test:x") == "sent" and ex.current() == ("sent", None))
    check("again: nothing", ex.start("X_ON", "d2", "On", "b") is None and len(pushes) == 1)
    check("end pushes", ex.end("X_OFF", "d3", "Off", "b") is True and pushes[-1] == ("Off", ""))
    check("cleared", store.get_state("test:x") == "" and ex.end("X_OFF", "d", "Off", "b") is False)

    # --- a failed start isn't saved and is tried again
    fail.append(1)
    check("failed: not saved", ex.start("X_ON", "d4", "On", "b") is None and ex.current() == (None, None))
    fail.clear()
    check("retried", ex.start("X_ON", "d5", "On", "b") == "sent")
    ex.end()
    check("end without an event: no push", len(pushes) == 3 and ex.current() == (None, None))

    # --- bands: another band is a new excursion
    check("high", ex.start("X_ON", "h", "High", "b", band="high") == "sent" and store.get_state("test:x") == "sent:high")
    check("high again: nothing", ex.start("X_ON", "h", "High", "b", band="high") is None)
    check("low: pushed", ex.start("X_ON", "l", "Low", "b", band="low") == "sent" and ex.current() == ("sent", "low"))

    # --- muted: saved, the end only logged
    ex.end()
    store.set_state("notify:nodes", "0")
    check("muted start", ex.start("X_ON", "m", "On", "b") == "muted" and store.get_state("test:x") == "muted")
    store.set_state("notify:nodes", "1")
    n = len(pushes)
    ex.end("X_OFF", "back", "Off", "b")
    check("muted end: no push, logged", len(pushes) == n and events("X_OFF")[-1] == "no push (alert was muted), back")

    # --- postponed by quiet hours: not saved, pushed after them
    quiet = alerts.Excursion("test:q", "nodes", "dev", {"event": "Q", "device": "dev"}, postpone_quiet=True)
    store.set_state("quiet_hours", "1"); store.set_state("quiet_start", "00:00"); store.set_state("quiet_end", "23:59")
    check("quiet hours: held back", quiet.start("Q_ON", "q", "Q", "b") is None and quiet.current() == (None, None))
    store.set_state("quiet_hours", "0")
    check("after them: pushed", quiet.start("Q_ON", "q", "Q", "b") == "sent")

    # --- cooldown key: one start per NOTIFICATION_COOLDOWN, also across excursions
    cool = alerts.Excursion("test:c", "nodes", "dev", {"event": "C", "device": "dev"}, cooldown_key="C_dev")
    check("first start", cool.start("C_ON", "c", "C", "b") == "sent")
    cool.end()
    check("within the cooldown: held", cool.start("C_ON", "c", "C", "b") is None)
    state.LAST_NOTIFICATION["C_dev"] = 0
    check("after it: pushed", cool.start("C_ON", "c", "C", "b") == "sent")

    # --- the old values are converted at startup
    old = {"watchdog:backup": "1", "watchdog:disk": "0", "sensor_silent:n:env": "m",
           "node_offline:n": "1", "node_offline:k": "m", "env_alerted:HUMIDITY_n": "high",
           "env_alerted:TEMP_n": "low", "env_alerted:PRESSURE": "falling", "baseline_alerted:temp": "sent"}
    for k, v in old.items():
        store.set_state(k, v)
    store.init_db()
    store.init_db()   # twice: the conversion only matches old values
    got = {k: store.get_state(k) for k in old}
    check(f"converted ({got})", got == {
        "watchdog:backup": "sent", "watchdog:disk": "", "sensor_silent:n:env": "muted",
        "node_offline:n": "sent", "node_offline:k": "muted", "env_alerted:HUMIDITY_n": "sent:high",
        "env_alerted:TEMP_n": "sent:low", "env_alerted:PRESSURE": "sent:falling", "baseline_alerted:temp": "sent"})
    check("node_offline loads", store.load_alert_state()[2] == {"n": True, "k": False})
