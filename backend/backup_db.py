"""Nightly backup of data.db (#78).

Copies the database with SQLite's online backup API (safe while the backend is
writing), checks the copy, gzips it to backups/data-YYYY-MM-DD.db.gz and keeps
the newest KEEP files. Run from the backend directory (the systemd timer in
backend/systemd/ does this), or: python3 backup_db.py [--db data.db] [--dir backups] [--keep 7]

Off-site copy (#122): when .env sets BACKUP_S3_BUCKET, the new file is also uploaded to
S3 (one PutObject, so the server's key can only add files, not read or delete them) and
backups/.offsite-ok is touched; the backend's watchdog pushes when that gets too old.
A failed upload exits 1; the local backup is kept either way.

Restore: stop iot-backend, `gunzip -c backups/data-<date>.db.gz > data.db`, start it.
Only the standard library, plus python-dotenv and boto3 for the upload.
"""
import argparse
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

OFFSITE_MARKER = ".offsite-ok"   # same name as config.OFFSITE_MARKER (the watchdog reads it)


def backup(db, out_dir, keep):
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "data.db.tmp"
    target = out_dir / f"data-{date.today().isoformat()}.db.gz"

    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"integrity check of the copy failed: {ok}")

    with open(tmp, "rb") as f_in, gzip.open(target, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp.unlink()

    # Same date run twice overwrites; the names sort by date
    old = sorted(out_dir.glob("data-*.db.gz"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    print(f"backup: {target} ({target.stat().st_size} bytes), removed {len(old)} old")
    return target


def upload(target, bucket, prefix):
    """Put [target] into s3://bucket/prefix<name> and touch the marker next to it. Credentials
    and region come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION."""
    import boto3   # only needed with an off-site bucket
    from botocore.config import Config
    s3 = boto3.client("s3", config=Config(connect_timeout=20, read_timeout=120,
                                          retries={"max_attempts": 5, "mode": "standard"}))
    key = prefix + target.name
    with open(target, "rb") as f:
        s3.put_object(Bucket=bucket, Key=key, Body=f)
    (target.parent / OFFSITE_MARKER).write_text(f"s3://{bucket}/{key}\n")
    print(f"backup: uploaded to s3://{bucket}/{key}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Back up the backend's SQLite database.")
    p.add_argument("--db", default="data.db", type=Path)
    p.add_argument("--dir", default="backups", type=Path)
    p.add_argument("--keep", default=7, type=int, help="number of daily backups to keep")
    a = p.parse_args()
    if not a.db.exists():
        sys.exit(f"backup: {a.db} not found")
    target = backup(a.db, a.dir, a.keep)
    load_dotenv(".env")   # the backend's .env, from the working directory
    bucket = os.environ.get("BACKUP_S3_BUCKET", "")
    if bucket:
        try:
            upload(target, bucket, os.environ.get("BACKUP_S3_PREFIX", "backups/"))
        except Exception as e:
            sys.exit(f"backup: off-site upload failed: {e}")
