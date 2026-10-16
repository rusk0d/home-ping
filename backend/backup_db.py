"""Nightly backup of data.db (#78).

Copies the database with SQLite's online backup API (safe while the backend is
writing), checks the copy, gzips it to backups/data-YYYY-MM-DD.db.gz and keeps
the newest KEEP files. Run from the backend directory (the systemd timer in
backend/systemd/ does this), or: python3 backup_db.py [--db data.db] [--dir backups] [--keep 7]

Restore: stop iot-backend, `gunzip -c backups/data-<date>.db.gz > data.db`, start it.
Uses only the standard library.
"""
import argparse
import gzip
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path


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


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Back up the backend's SQLite database.")
    p.add_argument("--db", default="data.db", type=Path)
    p.add_argument("--dir", default="backups", type=Path)
    p.add_argument("--keep", default=7, type=int, help="number of daily backups to keep")
    a = p.parse_args()
    if not a.db.exists():
        sys.exit(f"backup: {a.db} not found")
    backup(a.db, a.dir, a.keep)
