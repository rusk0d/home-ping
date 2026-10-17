"""#14 part 1: dead endpoints gone, used ones still answer.
"""

from support import check


def test_issue14_endpoints(env):
    import base64
    import server   # no google/requests stubs: the import must work without them
    import api
    server.prepare()   # importing does no work (#73)
    c = api.app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    for p in ["/api/latest2", "/api/history", "/api/stats24h", "/api/occupancy_rolling",
              "/api/presence/latest", "/api/presence/history", "/api/widget", "/api/next-events"]:
        check(f"{p} removed (404)", c.get(p, headers=auth).status_code == 404)
    for p in ["/api/latest", "/api/latest_all", "/api/devices", "/api/stats24h_combined", "/api/restarts",
              "/api/air", "/api/hourly", "/api/occupancy_status"]:
        r = c.get(p, headers=auth)
        check(f"{p} 200", r.status_code == 200)
    check("POST occupancy_arm 200", c.post("/api/occupancy_arm", json={"armed": True}, headers=auth).status_code == 200)
    check("register_token removed (#172) -> 404", c.post("/api/register_token", json={"token": "t"}, headers=auth).status_code == 404)
    check("auth still required", c.get("/api/latest").status_code == 401)
