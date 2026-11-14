"""#476: one "Home offline" push when every node is silent; no node check while the backend
has lost the broker.
"""
import sqlite3
import time
import types

from support import check


def test_home_offline(env):
    import server
    import config, state, store, alerts, nodes, ingest
    server.prepare()
    m = alerts.messaging
    limit = config.NODE_OFFLINE_ALERT_SECONDS
    a, b, c = list(config.DEVICES)[:3]

    def pushes():
        return [(x.kwargs["data"]["device"], x.kwargs["data"]["status"]) for x in m.Message.call_args_list
                if x.kwargs.get("data", {}).get("event") == "NODE_STATUS"]

    def seen(**ago):
        con = sqlite3.connect(config.DB)
        for dev, s in ago.items():
            con.execute("INSERT OR REPLACE INTO devices VALUES (?, datetime('now',?))", (dev, f"-{s} seconds"))
        con.commit(); con.close()

    def message(dev):
        ingest.on_message(None, None, types.SimpleNamespace(
            retain=False, topic=f"home/esp32/{dev}/status", payload=b"online"))

    def past_wait():
        state.STARTED_AT = time.time() - limit - 1
        state.NODES_RESUMED_AT = 0.0

    def cooldown_over(dev):   # a second outage within 15 min waits for the cooldown, like a node's
        alerts.set_cooldown(f"NODE_{dev}", time.time() - config.NOTIFICATION_COOLDOWN - 1)

    past_wait()

    # One node over the limit while the others have been silent for a while too: wait for them
    seen(**{a: limit + 10, b: limit - 20, c: limit - 60})
    nodes.check_nodes()
    check("a node over the limit waits while every other node is silent too", pushes() == [])

    # All over the limit: one push, not three
    seen(**{a: limit + 100, b: limit + 70, c: limit + 30})
    nodes.check_nodes(); nodes.check_nodes()
    check("every node silent: one 'Home offline' push", pushes() == [("home", "offline")])
    check("...no node marked offline on its own", not state.NODE_OFFLINE)
    check("...persisted", store.get_state("home_offline") == "sent")

    # A restart during the outage: no second push
    server.prepare(); m = alerts.messaging; m.Message.reset_mock(); past_wait()
    check("after a restart: still a home outage", state.HOME_OFFLINE)
    nodes.check_nodes()
    check("after a restart: no repeat", pushes() == [])

    # The first message ends it; the nodes not back yet get time
    message(b)
    check("first message: one 'Home back online'", pushes() == [("home", "online")])
    check("...state cleared", not state.HOME_OFFLINE and store.get_state("home_offline") == "")
    nodes.check_nodes()
    check("the other nodes get the limit's time to come back", pushes() == [("home", "online")])
    past_wait(); message(c)
    nodes.check_nodes()
    check("a node still silent after that is pushed on its own", pushes()[1:] == [(a, "offline")])
    message(a)
    check("...and back", pushes()[2:] == [(a, "online")])
    events = {r[0] for r in sqlite3.connect(config.DB).execute("SELECT type FROM events WHERE device = 'home'")}
    check("events logged", events == {"HOME_OFFLINE", "HOME_ONLINE"})

    # The backend loses the broker: nothing is checked, and the nodes get time after it's back
    m.Message.reset_mock()
    seen(**{a: limit + 100, b: limit + 100, c: limit + 100})
    ok = types.SimpleNamespace(is_failure=False)
    ingest.on_disconnect(None, None, None, ok, None)
    nodes.check_nodes()
    check("broker connection down: no push", pushes() == [] and not state.HOME_OFFLINE)
    client = types.SimpleNamespace(subscribe=lambda t: None, publish=lambda *x, **k: None)
    state.mqttc = client
    ingest.on_connect(client, None, None, ok, None)
    nodes.check_nodes()
    check("just reconnected: no push yet", pushes() == [])
    past_wait(); cooldown_over("home"); nodes.check_nodes()
    check("still silent the limit's time after the reconnect: pushed", pushes() == [("home", "offline")])

    # Quiet hours postpone it; back before they end: no push at all, but the wait still starts
    from support import quiet_now
    message(a); m.Message.reset_mock(); past_wait()
    quiet_now()
    seen(**{a: limit + 5, b: limit + 5, c: limit + 5})
    nodes.check_nodes()
    check("quiet hours: postponed", pushes() == [] and state.HOME_OFFLINE)
    message(a)
    nodes.check_nodes()
    check("back within quiet hours: no push, and no per-node push straight after", pushes() == [])
    quiet_now(False)

    # A single known node is never "the home"
    con = sqlite3.connect(config.DB); con.execute("DELETE FROM devices WHERE device != ?", (a,)); con.commit(); con.close()
    seen(**{a: limit + 5}); past_wait(); cooldown_over(a); nodes.check_nodes()
    check("one node only: its own push", pushes() == [(a, "offline")])
