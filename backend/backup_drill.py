"""Monthly restore drill (#339): is the newest off-site backup really one we could restore?

Downloads the newest backups/data-*.db.gz from the S3 bucket (#122) with a read-only key of
its own (DRILL_AWS_ACCESS_KEY_ID / DRILL_AWS_SECRET_ACCESS_KEY in .env; the upload key stays
put-only), restores it into a temp folder and checks it:
- gunzip, PRAGMA integrity_check
- the main tables are there and readings / hourly have rows (schema_version is only reported:
  a backup from before #306 has none, and init_db() migrates it like any older database)
- started like the backend would start it (#347): store.init_db() (schema, the migrations an
  older backup still needs) and rollup_hourly() as of its newest reading. Not prune(): that
  works on the real time and would delete raw rows the copy's hourly rows are checked against.
- tools/validate_db.py against the started copy, at the copy's own time (--now: its newest reading)
- the backup is recent (#356): uploaded within MAX_BACKUP_AGE_HOURS. An older one is still
  restored and checked, but the drill fails: it proved an old backup, and uploads have stopped
The live data.db is never touched. The result goes to backups/.drill.json, which the backend's
watchdog reads (push when it failed or is overdue, the app's Server card); exit 1 on a failure.

Run from the backend directory (the systemd timer in backend/systemd/ does this, monthly):
python3 backup_drill.py [--dir backups]. Only the standard library, python-dotenv and boto3.
"""
import argparse
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

RESULT = ".drill.json"   # same name as config.DRILL_RESULT (the watchdog reads it)
TABLES = ("readings", "presence_readings", "hourly", "events", "devices", "system_state")
NOT_EMPTY = ("readings", "hourly")
MAX_BACKUP_AGE_HOURS = 48   # nightly uploads (#122): two missed nights is more than a late run
BACKEND = Path(__file__).resolve().parent
VALIDATE = BACKEND / "tools" / "validate_db.py"
# Run in the copy's folder (store opens config.DB, "data.db", there): what server.prepare() does
# first, without prune() (see the docstring). Prints the schema_version it ends at.
START = (
    "import sys\n"
    "from datetime import timedelta\n"
    "import store\n"
    "store.init_db()\n"
    "store.rollup_hourly(now=store.parse_ts(sys.argv[1]) + timedelta(seconds=1))\n"
    "print(store.get_state('schema_version', ''))\n"
)


def newest_backup(s3, bucket, prefix):
    """The newest data-*.db.gz object under [prefix]: its listing entry, or None."""
    newest = None
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = obj["Key"][len(prefix):]
            if name.startswith("data-") and name.endswith(".db.gz") and (
                    newest is None or obj["LastModified"] > newest["LastModified"]):
                newest = obj
    return newest


def check_copy(db):
    """Integrity and sanity of the restored [db]: (facts, problem or None)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            return {}, f"integrity check failed: {ok}"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in TABLES if t not in tables]
        if missing:
            return {}, f"tables missing: {', '.join(missing)}"
        rows = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        empty = [t for t in NOT_EMPTY if not rows[t]]
        version = conn.execute("SELECT value FROM system_state WHERE key = 'schema_version'").fetchone()
        newest = conn.execute("SELECT MAX(ts) FROM readings").fetchone()[0]
        facts = {"rows": rows, "schema_version": version and version[0], "newest_reading": newest}
        if empty:
            return facts, f"empty: {', '.join(empty)}"
        return facts, None
    finally:
        conn.close()


def start_copy(db, at):
    """init_db() and the roll-up on [db] (named data.db) as of [at]: (worked, schema_version or
    the error's last lines)."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", START, at], cwd=db.parent, env=env,
                       capture_output=True, text=True, timeout=600)
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, (out[-1] if r.returncode == 0 and out else "\n".join(out[-5:]))


def validate(db, at):
    """tools/validate_db.py on [db] as of [at]: (passed, its last lines)."""
    r = subprocess.run([sys.executable, str(VALIDATE), "--db", str(db), "--now", at],
                       capture_output=True, text=True, timeout=600)
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, out[-12:]


def drill(s3, bucket, prefix, workdir):
    """The drill itself: the result dict (ok, error, and what it found)."""
    result = {"ok": False, "error": None, "bucket": bucket}
    obj = newest_backup(s3, bucket, prefix)
    if obj is None:
        result["error"] = f"no backup found under s3://{bucket}/{prefix}"
        return result
    age_h = (datetime.now(timezone.utc) - obj["LastModified"]).total_seconds() / 3600
    result.update(object=obj["Key"], uploaded=obj["LastModified"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                  size_bytes=obj["Size"], backup_age_hours=round(age_h, 1))
    gz = workdir / "backup.db.gz"
    db = workdir / "data.db"   # the name store / config.DB expect (start_copy runs in workdir)
    s3.download_file(bucket, obj["Key"], str(gz))
    try:
        with gzip.open(gz, "rb") as f_in, open(db, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    except (OSError, EOFError) as e:
        result["error"] = f"gunzip failed: {e}"
        return result
    result["restored_bytes"] = db.stat().st_size
    try:
        facts, problem = check_copy(db)
    except sqlite3.DatabaseError as e:
        facts, problem = {}, f"not a usable database: {e}"
    result.update(facts)
    if problem:
        result["error"] = problem
        return result
    started, info = start_copy(db, facts["newest_reading"])
    if not started:
        result["error"] = f"init_db/rollup failed: {info}"
        return result
    result["schema_version_started"] = info
    passed, lines = validate(db, facts["newest_reading"])
    result["validate"] = lines
    if not passed:
        result["error"] = "validate_db.py failed: " + next(
            (line.strip() for line in lines if "FAILURES" in line), "see validate")
        return result
    if age_h > MAX_BACKUP_AGE_HOURS:   # restorable, but old (#356)
        result["error"] = (f"newest backup is {age_h / 24:.1f} days old ({obj['Key']}): "
                           "are the uploads still running?")
        return result
    result["ok"] = True
    return result


def write_result(out_dir, result):
    """backups/.drill.json, replaced atomically (the watchdog may read it at any moment)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result["ran"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = out_dir / (RESULT + ".tmp")
    tmp.write_text(json.dumps(result, indent=1) + "\n")
    os.replace(tmp, out_dir / RESULT)


def client():
    """The drill's own read-only S3 client (region from AWS_DEFAULT_REGION)."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", aws_access_key_id=os.environ["DRILL_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["DRILL_AWS_SECRET_ACCESS_KEY"],
        config=Config(connect_timeout=20, read_timeout=120, retries={"max_attempts": 5, "mode": "standard"}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Restore the newest S3 backup into a temp DB and check it.")
    p.add_argument("--dir", default="backups", type=Path, help="where .drill.json goes")
    a = p.parse_args()
    load_dotenv(".env")   # the backend's .env, from the working directory
    bucket = os.environ.get("BACKUP_S3_BUCKET", "")
    if not bucket or not os.environ.get("DRILL_AWS_ACCESS_KEY_ID"):
        sys.exit("drill: needs BACKUP_S3_BUCKET and DRILL_AWS_ACCESS_KEY_ID / DRILL_AWS_SECRET_ACCESS_KEY in .env")
    with tempfile.TemporaryDirectory(prefix="drill-") as tmp:
        try:
            result = drill(client(), bucket, os.environ.get("BACKUP_S3_PREFIX", "backups/"), Path(tmp))
        except Exception as e:   # S3 errors (access denied, network) included: that's a failed drill
            result = {"ok": False, "error": f"{type(e).__name__}: {e}", "bucket": bucket}
    write_result(a.dir, result)
    if result["ok"]:
        print(f"drill: OK, {result['object']} ({result['size_bytes']} bytes) restored and validated, "
              f"{result['rows']['readings']} readings, {result['rows']['hourly']} hours")
    else:
        sys.exit(f"drill: FAILED: {result['error']}")
