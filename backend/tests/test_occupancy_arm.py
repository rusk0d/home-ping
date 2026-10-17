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
