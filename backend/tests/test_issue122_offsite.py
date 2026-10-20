"""#122: the nightly backup is also uploaded to S3 (a fake boto3 here), and the watchdog
pushes when no upload succeeded for too long.
"""

import os
import subprocess
import sys
import time

from support import BACKEND, check

FAKE_BOTO3 = '''
import json, os
class _S3:
    def put_object(self, Bucket, Key, Body):
        if os.environ.get("FAKE_S3_FAIL"):
            raise Exception("AccessDenied")
        with open("uploaded.json", "w") as f:
            json.dump({"bucket": Bucket, "key": Key, "size": len(Body.read())}, f)
def client(name, config=None):
    assert name == "s3"
    return _S3()
'''


def test_issue122_offsite(env):
    import json, sqlite3
    con = sqlite3.connect("data.db")
    con.execute("CREATE TABLE readings (v REAL)")
    con.commit(); con.close()

    fakes = env / "fakes"
    (fakes / "boto3").mkdir(parents=True)
    (fakes / "botocore").mkdir()
    (fakes / "boto3" / "__init__.py").write_text(FAKE_BOTO3)
    (fakes / "botocore" / "__init__.py").write_text("")
    (fakes / "botocore" / "config.py").write_text("def Config(**kw):\n    return kw\n")

    def run(**extra):
        e = dict(os.environ, PYTHONPATH=str(fakes), **extra)
        r = subprocess.run([sys.executable, str(BACKEND / "backup_db.py")], capture_output=True, text=True, env=e)
        print(r.stdout.strip(), r.stderr.strip())
        return r

    marker = env / "backups" / ".offsite-ok"
    r = run()
    check("no bucket: local backup only", r.returncode == 0 and not (env / "uploaded.json").exists() and not marker.exists())

    r = run(BACKUP_S3_BUCKET="bucket")
    up = json.loads((env / "uploaded.json").read_text())
    name = next((env / "backups").glob("data-*.db.gz")).name
    check("uploaded under backups/", r.returncode == 0 and up["bucket"] == "bucket" and up["key"] == "backups/" + name)
    check("the whole gz file", up["size"] == (env / "backups" / name).stat().st_size)
    check("marker names the object", marker.read_text().strip() == f"s3://bucket/backups/{name}")

    os.utime(marker, (1, 1))
    r = run(BACKUP_S3_BUCKET="bucket", FAKE_S3_FAIL="1")
    check("failed upload: exit 1 with the reason", r.returncode == 1 and "off-site upload failed: AccessDenied" in r.stderr)
    check("...marker untouched, local backup kept", marker.stat().st_mtime == 1 and (env / "backups" / name).exists())


def test_issue122_offsite_watchdog(env):
    import sqlite3
    os.environ["BACKUP_S3_BUCKET"] = "bucket"
    import server
    import config, store, alerts, server_checks
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [(c.kwargs["data"]["device"], c.kwargs["data"]["title"]) for c in m.Message.call_args_list
               if c.kwargs.get("data", {}).get("event") == "WATCHDOG"]
        m.Message.reset_mock()
        return out

    backups = env / config.BACKUP_DIR
    server_checks.check_watchdogs()
    check("no backup folder: no check", pushes() == [])

    backups.mkdir()
    (backups / "data-2026-10-20.db.gz").write_bytes(b"x")
    server_checks.check_watchdogs()
    check("never uploaded: 'Off-site backup failed'", pushes() == [("offsite", "Off-site backup failed")])
    con = sqlite3.connect(config.DB)
    body = [r[0] for r in con.execute("SELECT value FROM events WHERE type='WATCHDOG_OFFSITE'")]
    con.close()
    check("event says 'last: never'", any("last: never" in b for b in body))

    marker = backups / config.OFFSITE_MARKER
    marker.write_text("s3://bucket/backups/data-2026-10-20.db.gz\n")
    server_checks.check_watchdogs()
    check("upload again: 'Off-site backup OK again'", pushes() == [("offsite", "Off-site backup OK again")])

    t = time.time() - (config.OFFSITE_MAX_AGE_HOURS + 1) * 3600
    os.utime(marker, (t, t))
    server_checks.check_watchdogs()
    check("27 h old: pushes again, local backup still fine", pushes() == [("offsite", "Off-site backup failed")])

    server_checks.BACKUP_S3_BUCKET = ""
    store.set_state("watchdog:offsite", "0")
    server_checks.check_watchdogs()
    check("no bucket: no check", pushes() == [])

