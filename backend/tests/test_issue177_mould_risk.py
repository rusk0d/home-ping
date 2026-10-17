"""#177: /api/air mould_risk: each of the last 3 complete hours above 60 %RH.
"""

from support import check


def test_issue177_mould_risk(env):
    import base64, sqlite3
    import server, config, api
    server.prepare()
    c = api.app.test_client(); A = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    risk = lambda: c.get("/api/air", headers=A).get_json()["mould_risk"]

    def hours(*values):
        """One reading in the middle of each of the last len(values) complete hours, oldest first."""
        con = sqlite3.connect(config.DB)
        con.execute("DELETE FROM readings")
        for i, v in enumerate(values):
            ago = len(values) - i   # hours back from the start of the current hour
            con.execute("""INSERT INTO readings (device, ts, humidity, pressure)
                           VALUES ('home_ping_esp32', datetime(strftime('%Y-%m-%d %H:30:00','now'), ?), ?, 1010)""",
                        (f"-{ago} hours", v))
        con.commit(); con.close()

    check("no data: false", risk() is False)
    hours(65, 70, 62)
    check("3 humid hours: true", risk() is True)
    hours(65, 59, 70)
    check("one hour at 59 %: false", risk() is False)
    hours(70, 70)
    check("only 2 hours of data: false", risk() is False)
    hours(60.0, 61, 61)
    check("exactly 60 % is not above: false", risk() is False)
    hours(80, 65, 70, 62)   # the 4th hour back doesn't matter
    check("older hours ignored: true", risk() is True)
    con = sqlite3.connect(config.DB)
    con.execute("INSERT INTO readings (device, ts, humidity) VALUES ('x', datetime('now'), 30)"); con.commit(); con.close()
    check("the current (incomplete) hour doesn't count", risk() is True)
