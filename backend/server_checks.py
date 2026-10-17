"""Server watchdog pushes (#176): the nightly backup is overdue, or the disk is nearly full.

UptimeRobot only sees /api/health, so these would otherwise go unnoticed until data is lost.
check_watchdogs() runs in the summaries loop (every 5 min). Each check pushes once per
excursion and once more when it's over, under the "nodes" notification switch (#82).
Quiet hours postpone the push to the next check after they end instead of dropping it:
nothing here is urgent, but it mustn't get lost (the backup runs at 03:30, inside the
default quiet hours). State in system_state `watchdog:<name>`: '1' pushed, 'm' muted
(switch off), '0' fine.
"""

import logging
import shutil
import time
from pathlib import Path

from config import BACKUP_DIR, BACKUP_MAX_AGE_HOURS, DB, DISK_FREE_HYSTERESIS_MB, DISK_MIN_FREE_MB
from store import get_state, set_state
from alerts import alert, alert_muted

log = logging.getLogger(__name__)


def backup_age_hours(now=None):
    """Hours since the newest backup; None when there is no backup folder (dev, tests)."""
    folder = Path(BACKUP_DIR)
    if not folder.is_dir():
        return None
    times = [f.stat().st_mtime for f in folder.glob("data-*.db.gz")]
    if not times:
        return float("inf")
    return ((now or time.time()) - max(times)) / 3600


def disk_free_mb():
    """Free space on the database's disk, MB."""
    return shutil.disk_usage(Path(DB).resolve().parent).free / 2**20


def _watch(name, bad, recovered, title, body, back_title, back_body):
    """One check: push when [bad] starts, and again once [recovered] (with hysteresis)."""
    key = f"watchdog:{name}"
    status = get_state(key, "0")
    data = {"event": "WATCHDOG", "device": name}
    if status == "0" and bad:
        if alert_muted("nodes") == "quiet hours":
            return   # try again after quiet hours
        result = alert("server", f"WATCHDOG_{name.upper()}", body, title, body, data, kind="nodes")
        if result:   # a failed send is retried at the next check
            set_state(key, "1" if result == "sent" else "m")
    elif status != "0" and recovered:
        set_state(key, "0")
        if status == "1":   # only clear a notification that was actually shown
            alert("server", f"WATCHDOG_{name.upper()}_OK", back_body, back_title, back_body, data, kind="nodes")


def check_watchdogs(now=None):
    age = backup_age_hours(now)
    if age is not None:
        last = "never" if age == float("inf") else f"{age / 24:.1f} days ago" if age >= 48 else f"{age:.0f} h ago"
        _watch("backup", age > BACKUP_MAX_AGE_HOURS, age <= BACKUP_MAX_AGE_HOURS,
               "Backup failed", f"No database backup for over {BACKUP_MAX_AGE_HOURS} h (last: {last})",
               "Backup OK again", "The nightly database backup ran again")
    free = disk_free_mb()
    _watch("disk", free < DISK_MIN_FREE_MB, free >= DISK_MIN_FREE_MB + DISK_FREE_HYSTERESIS_MB,
           "Disk almost full", f"Only {free:.0f} MB free on the server",
           "Disk space OK again", f"{free:.0f} MB free on the server")
