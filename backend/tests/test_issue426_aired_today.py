"""#426: /api/air's aired_today: the window spells that ended since local midnight, with the
minutes their WINDOW_CLOSED logged, plus one still open counted up to now."""

import base64
from datetime import timedelta

from support import check


def test_issue426_aired_today(env):
    import server
    import api, store
    from store import db
    server.prepare()

    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()
    aired = lambda: client.get("/api/air", headers=auth).get_json()["aired_today"]

    def closed(value, when="datetime('now')"):
        with db() as conn:
            conn.execute(f"INSERT INTO events (device, ts, type, value) VALUES ('home_ping_esp32', {when}, 'WINDOW_CLOSED', ?)",
                         (value,))

    check("nothing yet", aired() == {"count": 0, "minutes": 0})
    # Yesterday's (an hour before local midnight) doesn't count
    closed("after 30 min (temperature rising), inside 21.0 °C", "datetime('now', 'localtime', 'start of day', '-1 hours', 'utc')")
    check("yesterday's: not counted", aired() == {"count": 0, "minutes": 0})
    closed("after 13 min (temperature rising), inside 21.6 °C, CO₂ 480 ppm")
    closed("after 9 min (CO₂ rising), inside 22.0 °C")
    check(f"two today ({aired()})", aired() == {"count": 2, "minutes": 22})
    closed("garbled")
    check("one without a duration still counts", aired() == {"count": 3, "minutes": 22})
    # One still open, 12 min in
    store.set_state("air:window", f"{(store.utcnow() - timedelta(minutes=12)):%Y-%m-%d %H:%M:%S}|23.0")
    check(f"plus the open one ({aired()})", aired() == {"count": 4, "minutes": 34})
