"""#396: "Window still open?" pushes once per window spell, not on every location arm (GPS
jitter at the geofence edge, or a retried arm). A failed send is tried again on the next leave."""

import base64
from datetime import timedelta

from support import check


def test_issue396_window_left_once(env):
    import server
    import alerts, api, store
    server.prepare()

    pushes = []
    ok = [True]
    alerts.send_push = lambda title, body, data=None: pushes.append(title) or ok[0]
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    def leave():
        r = c.post("/api/occupancy_arm", json={"armed": True, "source": "location"}, headers=auth)
        assert r.status_code == 200

    def spell(minutes_ago):
        store.set_state("air:window", f"{(store.utcnow() - timedelta(minutes=minutes_ago)):%Y-%m-%d %H:%M:%S}|23.0")

    spell(12)
    leave()
    check("first leave: pushed", pushes == ["Window still open?"])
    leave()
    leave()
    check("jitter / retried arm in the same spell: no more pushes", len(pushes) == 1)

    # A new spell pushes again
    spell(3)
    leave()
    check("a new spell: pushed again", len(pushes) == 2)

    # A failed send isn't remembered, so the next leave tries again
    spell(5)
    ok[0] = False
    leave()
    check("failed: tried", len(pushes) == 3)
    ok[0] = True
    leave()
    check("...and tried again on the next leave", len(pushes) == 4)
    leave()
    check("...then once", len(pushes) == 4)
