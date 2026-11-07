"""#339: the monthly restore drill restores the newest S3 backup (a fake boto3 here) into a
temp DB and validates it; the watchdog pushes when it failed or is overdue."""

import gzip
import json
import os
import subprocess
import sys
import time

from support import BACKEND, check

# A bucket that is a folder (FAKE_S3_DIR): each file is an object, its mtime LastModified
FAKE_BOTO3 = '''
import os, shutil
from datetime import datetime, timezone
ROOT = os.environ["FAKE_S3_DIR"]
class _Pages:
    def paginate(self, Bucket, Prefix):
        objs = []
        for name in sorted(os.listdir(ROOT)):
            st = os.stat(os.path.join(ROOT, name))
            objs.append({"Key": Prefix + name, "Size": st.st_size,
                         "LastModified": datetime.fromtimestamp(st.st_mtime, timezone.utc)})
        return [{"Contents": objs}] if objs else [{}]
class _S3:
    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Pages()
    def download_file(self, bucket, key, path):
        shutil.copy(os.path.join(ROOT, key.split("/")[-1]), path)
def client(name, aws_access_key_id=None, aws_secret_access_key=None, config=None):
    assert name == "s3" and aws_access_key_id == "drill-key", "the drill's own key"
    return _S3()
'''


def _fakes(env):
    fakes = env / "fakes"
    (fakes / "boto3").mkdir(parents=True)
    (fakes / "botocore").mkdir()
    (fakes / "boto3" / "__init__.py").write_text(FAKE_BOTO3)
    (fakes / "botocore" / "__init__.py").write_text("")
    (fakes / "botocore" / "config.py").write_text("def Config(**kw):\n    return kw\n")
    return fakes


def test_issue339_restore_drill(env):
    import server
    import config, store
    from datetime import timedelta
    from store import db, utcnow
    server.prepare()

    # A database worth restoring: readings over the last hours, rolled up like the backend does
    now = utcnow().replace(microsecond=0)
    with db() as conn:
        for minutes in range(0, 180, 5):
            conn.execute("INSERT INTO readings (device, ts, temp, humidity) VALUES ('home_ping_esp32', ?, 21, 45)",
                         ((now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),))
        conn.execute("DELETE FROM system_state WHERE key = 'hourly_until'")
    store.rollup_hourly()

    bucket = env / "bucket"
    bucket.mkdir()
    def upload(name, data, age_s=0):
        path = bucket / name
        path.write_bytes(data)
        t = time.time() - age_s
        os.utime(path, (t, t))

    fakes = _fakes(env)
    def run():
        e = dict(os.environ, PYTHONPATH=str(fakes), FAKE_S3_DIR=str(bucket), BACKUP_S3_BUCKET="b",
                 DRILL_AWS_ACCESS_KEY_ID="drill-key", DRILL_AWS_SECRET_ACCESS_KEY="s")
        r = subprocess.run([sys.executable, str(BACKEND / "backup_drill.py")], capture_output=True, text=True, env=e)
        print(r.stdout.strip(), r.stderr.strip())
        return r, json.loads((env / "backups" / config.DRILL_RESULT).read_text())

    r, res = run()
    check(f"empty bucket: failed ({res['error']})", r.returncode == 1 and not res["ok"] and "no backup found" in res["error"])

    good = gzip.compress((env / "data.db").read_bytes())
    upload("data-2026-11-01.db.gz", b"old one, never read", age_s=86400)
    upload("data-2026-11-02.db.gz", good)
    r, res = run()
    check(f"restored and validated ({res.get('error')})", r.returncode == 0 and res["ok"] and res["error"] is None)
    check("the newest object", res["object"] == "backups/data-2026-11-02.db.gz" and res["size_bytes"] == len(good))
    check(f"rows counted ({res.get('rows')})", res["rows"]["readings"] == 36 and res["rows"]["hourly"] >= 2)   # 2 or 3 complete hours, by the minute
    check("validate_db.py passed on the copy", any("All checks PASSED" in line for line in res["validate"]))
    check("run time recorded", res["ran"].endswith("Z"))

    check(f"started at the current version ({res.get('schema_version_started')})",
          res["schema_version_started"] == str(store.SCHEMA_VERSION))

    # A backup from before #306: no schema_version, only the old #65 flag; still good (init_db
    # migrates it like any older database)
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key = 'schema_version'")
        conn.execute("INSERT INTO system_state (key, value) VALUES ('ts_utc', '1')")
    upload("data-2026-11-02b.db.gz", gzip.compress((env / "data.db").read_bytes()))
    r, res = run()
    check(f"no schema_version: still OK ({res.get('error')})", r.returncode == 0 and res["ok"] and res["schema_version"] is None)

    # ...and one like the first real drill's (2026-11-07): from before the #290 fix and #331,
    # so the fridge is averaged into the room's hours and hourly has no fridge columns. Started
    # like the backend, migration 3 and the roll-up rebuild those hours: a good backup
    with db() as conn:
        fridge_ts = now - timedelta(minutes=90)
        conn.execute("INSERT INTO readings (device, ts, temp) VALUES (?, ?, 4.0)",
                     (config.FRIDGE_DEVICE, fridge_ts.strftime("%Y-%m-%d %H:%M:%S")))
        conn.execute("UPDATE hourly SET temp_avg = 17 WHERE hour >= ?", (fridge_ts.strftime("%Y-%m-%d %H:00:00"),))
        for col in ("fridge_avg", "fridge_min", "fridge_max"):
            conn.execute(f"ALTER TABLE hourly DROP COLUMN {col}")
    upload("data-2026-11-02c.db.gz", gzip.compress((env / "data.db").read_bytes()))
    r, res = run()
    check(f"fridge-mixed hours, no fridge columns: still OK ({res.get('error')}, {res.get('validate')})",
          r.returncode == 0 and res["ok"] and res["schema_version_started"] == str(store.SCHEMA_VERSION))

    upload("data-2026-11-03.db.gz", b"not gzip at all")
    r, res = run()
    check(f"corrupt gz: failed ({res['error']})", r.returncode == 1 and "gunzip failed" in res["error"])

    # A copy whose hourly rows don't match its raw rows, and no migration rebuilds them:
    # validate_db.py must catch it
    with db() as conn:
        conn.execute("UPDATE hourly SET temp_avg = temp_avg + 5")
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('schema_version', ?)",
                     (str(store.SCHEMA_VERSION),))
    upload("data-2026-11-04.db.gz", gzip.compress((env / "data.db").read_bytes()))
    r, res = run()
    check(f"validate_db failure: failed ({res['error']})", r.returncode == 1 and "validate_db.py failed" in res["error"])

    upload("data-2026-11-05.db.gz", gzip.compress(b"SQLite format 3\x00" + b"\x00" * 200))
    r, res = run()
    check(f"not a database: failed ({res['error']})", r.returncode == 1 and not res["ok"])


def test_issue339_drill_watchdog(env):
    os.environ.update(BACKUP_S3_BUCKET="bucket", DRILL_AWS_ACCESS_KEY_ID="drill-key")
    import server
    import config, alerts, server_checks
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [c.kwargs["data"]["title"] for c in m.Message.call_args_list
               if c.kwargs.get("data", {}).get("device") == "drill"]
        m.Message.reset_mock()
        return out

    def result(ok, days_ago=1, error=None):
        ran = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))
        (backups / config.DRILL_RESULT).write_text(json.dumps(
            {"ok": ok, "error": error, "object": "backups/data-2026-11-01.db.gz", "ran": ran}))

    backups = env / config.BACKUP_DIR
    server_checks.check_watchdogs()
    check("no backup folder: no check", pushes() == [] and server_checks.status()["drill"] is None)

    backups.mkdir()
    (backups / "data-2026-11-01.db.gz").write_bytes(b"x")
    (backups / config.OFFSITE_MARKER).write_text("s3://bucket/x\n")
    server_checks.check_watchdogs()
    check("never ran: overdue", pushes() == ["Restore drill overdue"])
    check("status: not ok", server_checks.status()["drill"] == {"last": None, "backup": None, "error": None, "ok": False})

    result(True)
    server_checks.check_watchdogs()
    check("ran fine: OK again", pushes() == ["Restore drill OK again"])
    st = server_checks.status()["drill"]
    check(f"status ok ({st})", st["ok"] is True and st["backup"] == "backups/data-2026-11-01.db.gz" and st["last"].endswith("Z"))

    result(False, error="integrity check failed: page 3")
    server_checks.check_watchdogs()
    check("failed: pushed once", pushes() == ["Restore drill failed"])
    server_checks.check_watchdogs()
    check("...not again", pushes() == [])
    result(True)
    server_checks.check_watchdogs()
    pushes()

    result(True, days_ago=config.DRILL_MAX_AGE_DAYS + 1)
    server_checks.check_watchdogs()
    check("36 days: overdue", pushes() == ["Restore drill overdue"] and server_checks.status()["drill"]["ok"] is False)

    (backups / config.DRILL_RESULT).write_text("{broken")
    check("unreadable result: a failed drill", server_checks.drill_result()["ok"] is False)

    server_checks.BACKUP_DRILL = False
    check("no drill key: no check", server_checks.status()["drill"] is None)
