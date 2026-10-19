"""Occupancy arm switch: POST /api/occupancy_arm and GET /api/occupancy_status.
"""

from support import check


def test_occupancy_arm(env):
    import base64
    import server
    import api
    server.prepare()   # importing does no work (#73)
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    check("status needs auth", c.get("/api/occupancy_status").status_code == 401)
    check("disarmed at first", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": False})
    check("arm -> armed", c.post("/api/occupancy_arm", json={"armed": True}, headers=auth).get_json() == {"armed": True})
    check("status armed", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": True})
    check("disarm -> disarmed", c.post("/api/occupancy_arm", json={"armed": False}, headers=auth).get_json() == {"armed": False})
    check("status disarmed", c.get("/api/occupancy_status", headers=auth).get_json() == {"armed": False})

    # The source is optional (the old app sends none) and goes into the event (#238)
    check("location arm -> armed", c.post("/api/occupancy_arm", json={"armed": True, "source": "location"}, headers=auth).get_json() == {"armed": True})
    check("unknown source -> 400", c.post("/api/occupancy_arm", json={"armed": True, "source": "cron"}, headers=auth).status_code == 400)
    import sqlite3, config
    rows = sqlite3.connect(config.DB).execute("SELECT value FROM events WHERE type='OCCUPANCY_ARMED' ORDER BY id").fetchall()
    check("event records the source", rows[-1] == ("1 (location)",))
    check("event without a source says manual", ("0 (manual)",) in rows or ("1 (manual)",) in rows)
