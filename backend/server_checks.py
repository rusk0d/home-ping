"""Server watchdog pushes (#176): the nightly backup (or its S3 copy, #122) is overdue, the disk is nearly full, or a
TLS certificate is about to expire (#206; renewal was broken for weeks without anyone noticing).

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
import socket
import ssl
import time
from pathlib import Path

from config import (BACKUP_DIR, BACKUP_MAX_AGE_HOURS, BACKUP_S3_BUCKET, OFFSITE_MARKER, OFFSITE_MAX_AGE_HOURS,
                    CERT_CHECK, CERT_CHECK_INTERVAL, CERT_MIN_DAYS, DB,
                    DISK_FREE_HYSTERESIS_MB, DISK_MIN_FREE_MB)
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


def offsite_age_hours(now=None):
    """Hours since the last upload to S3 (#122); None when no bucket is set or there is no
    backup folder."""
    marker = Path(BACKUP_DIR) / OFFSITE_MARKER
    if not BACKUP_S3_BUCKET or not marker.parent.is_dir():
        return None
    if not marker.exists():
        return float("inf")
    return ((now or time.time()) - marker.stat().st_mtime) / 3600


def _ago(hours):
    return "never" if hours == float("inf") else f"{hours / 24:.1f} days ago" if hours >= 48 else f"{hours:.0f} h ago"


def disk_free_mb():
    """Free space on the database's disk, MB."""
    return shutil.disk_usage(Path(DB).resolve().parent).free / 2**20


def cert_expiry(host, port, timeout=10):
    """When the certificate served at host:port expires (epoch seconds), checked like a client
    would (trusted chain, host name). A certificate that fails that (expired, wrong name, ...)
    returns the reason as a string. Raises OSError when the host can't be reached."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    except ssl.SSLCertVerificationError as e:
        return e.verify_message or str(e)


_certs_checked = 0.0   # a TLS handshake per target, so once an hour rather than every 5 min


def check_certs(now=None):
    global _certs_checked
    now = now or time.time()
    if now - _certs_checked < CERT_CHECK_INTERVAL:
        return
    _certs_checked = now
    for target in CERT_CHECK:
        host, _, port = target.rpartition(":")
        try:
            expires = cert_expiry(host, int(port))
        except (OSError, ValueError) as e:
            # Unreachable: nginx/Mosquitto down is UptimeRobot's and the nodes' job; no change here
            log.warning("certificate check %s failed: %s", target, e)
            continue
        if isinstance(expires, str):
            _watch(f"cert_{port}", True, False, "Certificate problem", f"{target}: {expires}", "", "")
            continue
        days = (expires - now) / 86400
        until = time.strftime("%Y-%m-%d", time.localtime(expires))
        _watch(f"cert_{port}", days < CERT_MIN_DAYS, days >= CERT_MIN_DAYS,
               "Certificate expires soon",
               f"{target} expires in {max(days, 0):.0f} days ({until}); is the renewal running?",
               "Certificate renewed", f"{target} is valid until {until}")


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
        _watch("backup", age > BACKUP_MAX_AGE_HOURS, age <= BACKUP_MAX_AGE_HOURS,
               "Backup failed", f"No database backup for over {BACKUP_MAX_AGE_HOURS} h (last: {_ago(age)})",
               "Backup OK again", "The nightly database backup ran again")
    age = offsite_age_hours(now)
    if age is not None:
        _watch("offsite", age > OFFSITE_MAX_AGE_HOURS, age <= OFFSITE_MAX_AGE_HOURS,
               "Off-site backup failed", f"No backup uploaded to S3 for over {OFFSITE_MAX_AGE_HOURS} h (last: {_ago(age)})",
               "Off-site backup OK again", "The database backup reached S3 again")
    free = disk_free_mb()
    _watch("disk", free < DISK_MIN_FREE_MB, free >= DISK_MIN_FREE_MB + DISK_FREE_HYSTERESIS_MB,
           "Disk almost full", f"Only {free:.0f} MB free on the server",
           "Disk space OK again", f"{free:.0f} MB free on the server")
    check_certs(now)
