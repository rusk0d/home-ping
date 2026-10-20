"""#260: GET /api/server_status, the watchdog's view for the app's Server card.
"""

import os
import time
from unittest import mock

from support import check


def test_issue260_server_status(env):
    os.environ["BACKUP_S3_BUCKET"] = "bucket"
    os.environ["CERT_CHECK"] = "www.example.com:443,example.com:8883"
    import server
    import config, state, store, api, server_checks
    server.prepare()
    store.init_db()
    c = api.app.test_client()
    auth = {"Authorization": "Basic dTpw"}   # u:p from the env fixture

    def get():
        r = c.get("/api/server_status", headers=auth)
        assert r.status_code == 200
        return r.get_json()

    check("needs auth", c.get("/api/server_status").status_code == 401)

    b = get()
    check("no backup folder: backup and offsite null", b["backup"] is None and b["offsite"] is None)
    check("certificates not checked yet: listed with ok null",
          [x["target"] for x in b["certs"]] == ["www.example.com:443", "example.com:8883"]
          and all(x["ok"] is None and x["expires"] is None for x in b["certs"]))
    check("disk has room", b["disk"]["ok"] is True and b["disk"]["free_mb"] > 0)
    check("backend: started as ISO UTC, MQTT not connected yet",
          b["backend"]["started"].endswith("Z") and b["backend"]["mqtt_connected"] is False
          and b["backend"]["last_message_age_s"] is None)

    backups = env / config.BACKUP_DIR
    backups.mkdir()
    b = get()
    check("empty folder: never backed up, never uploaded",
          b["backup"] == {"last": None, "size_bytes": None, "count": 0, "ok": False}
          and b["offsite"] == {"last": None, "ok": False})

    for name, hours_old in (("data-2026-10-19.db.gz", 25), ("data-2026-10-20.db.gz", 1)):
        f = backups / name
        f.write_bytes(b"x" * 100)
        t = time.time() - hours_old * 3600
        os.utime(f, (t, t))
    marker = backups / config.OFFSITE_MARKER
    marker.write_text("s3://bucket/backups/data-2026-10-20.db.gz\n")
    t = time.time() - (config.OFFSITE_MAX_AGE_HOURS + 1) * 3600
    os.utime(marker, (t, t))
    b = get()
    check("newest backup: time, size, count", b["backup"]["ok"] and b["backup"]["count"] == 2
          and b["backup"]["size_bytes"] == 100
          and b["backup"]["last"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((backups / "data-2026-10-20.db.gz").stat().st_mtime)))
    check("upload 27 h ago: offsite not ok", b["offsite"]["ok"] is False
          and b["offsite"]["last"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))

    now = time.time()
    expiry = {"www.example.com": now + 60 * 86400, "example.com": now + 5 * 86400}
    with mock.patch.object(server_checks, "cert_expiry", lambda host, port: expiry[host]):
        server_checks.check_certs(now)
    b = get()
    check("certificates: 60 days ok, 5 days not",
          [(x["ok"], x["error"]) for x in b["certs"]] == [(True, None), (False, None)]
          and b["certs"][0]["expires"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry["www.example.com"])))

    def unreachable(host, port):
        raise OSError("timed out")
    with mock.patch.object(server_checks, "cert_expiry", unreachable):
        server_checks.check_certs(now + config.CERT_CHECK_INTERVAL)
    b = get()
    check("unreachable: error, not ok", all(x["error"] == "unreachable" and x["ok"] is False for x in b["certs"]))

    state.mqttc = mock.MagicMock()
    state.mqttc.is_connected = lambda: True
    state.LAST_MESSAGE_AT = time.time() - 3
    b = get()
    check("backend: MQTT connected, last message 3 s ago",
          b["backend"]["mqtt_connected"] is True and b["backend"]["last_message_age_s"] == 3)

    server_checks.DISK_MIN_FREE_MB = server_checks.disk_free_mb() + 1000
    check("low disk: not ok", get()["disk"]["ok"] is False)
