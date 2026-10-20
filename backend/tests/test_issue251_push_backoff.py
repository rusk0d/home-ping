"""#251: a push that keeps failing is retried after 1, 2, 5, 10 min, then every 15 min,
not every minute; a success (or a mute) starts over.
"""

import sqlite3
import time
from unittest import mock

from support import check


def test_issue251_push_backoff(env):
    import server
    import config, state, alerts, nodes
    server.prepare()
    m = alerts.messaging
    key = "NODE_home_ping_esp32"

    def fail():
        m.send.side_effect = Exception("Temporary failure in name resolution")

    def succeed():
        m.send.side_effect = None

    def push():
        return alerts.alert("home_ping_esp32", "NODE_OFFLINE", "last seen x", title="t", body="b",
                            data={"event": "NODE_STATUS"}, cooldown_key=key, kind="nodes")

    def retry_in():
        return round(state.LAST_NOTIFICATION[key] + config.NOTIFICATION_COOLDOWN - time.time())

    fail()
    delays = []
    for _ in range(6):
        push()
        delays.append(retry_in())
    check(f"retries back off 1, 2, 5, 10, then 15 min (got {delays})",
          all(abs(d - want) <= 1 for d, want in zip(delays, (60, 120, 300, 600, 900, 900))))

    succeed()
    check("a success sends and starts the cooldown", push() == "sent" and abs(retry_in() - config.NOTIFICATION_COOLDOWN) <= 1)
    fail()
    push()
    check("...and the next failure starts the back-off over", abs(retry_in() - 60) <= 1)

    store_state = alerts.set_state
    store_state("notify:nodes", "0")
    push()
    store_state("notify:nodes", "1")
    push()
    check("a muted alert also starts over", abs(retry_in() - 60) <= 1)

    # The 2026-10-19 outage, replayed through the real offline check: both nodes silent for
    # an hour and every push failing. Every minute before; now 7 attempts per node.
    state.LAST_NOTIFICATION.clear(); state.PUSH_FAILURES.clear(); state.NODE_OFFLINE.clear()
    conn = sqlite3.connect(config.DB)
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM devices")
    for d in ("home_ping_esp32", "hallway_presence_esp32"):
        conn.execute("INSERT INTO devices (device, last_seen) VALUES (?, datetime('now', '-10 minutes'))", (d,))
    conn.commit()
    state.STARTED_AT = 0
    t0 = time.time()
    for minute in range(60):
        with mock.patch("time.time", return_value=t0 + minute * 60 + 1):
            nodes.check_nodes()
    attempts = conn.execute("SELECT device, COUNT(*) FROM events WHERE type = 'NODE_OFFLINE' GROUP BY device").fetchall()
    check(f"an hour of outage: 7 attempts per node, not 60 (got {attempts})",
          sorted(attempts) == [("hallway_presence_esp32", 7), ("home_ping_esp32", 7)])
