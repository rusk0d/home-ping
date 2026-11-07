"""Server watchdog pushes (#176): the nightly backup (or its S3 copy, #122) is overdue, the disk is nearly full, or a
TLS certificate is about to expire (#206; renewal was broken for weeks without anyone noticing).

UptimeRobot only sees /api/health, so these would otherwise go unnoticed until data is lost.
check_watchdogs() runs in the summaries loop (every 5 min). Each check pushes once per
excursion and once more when it's over, under the "nodes" notification switch (#82).
Quiet hours postpone the push to the next check after they end instead of dropping it:
nothing here is urgent, but it mustn't get lost (the backup runs at 03:30, inside the
default quiet hours). State in system_state `watchdog:<name>`, an alerts.Excursion (#305):
'sent', 'muted' (switch off), '' fine. status() is the same view for the app's Server card (#260).
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
import state
from alerts import Excursion

log = logging.getLogger(__name__)


def backups():
    """The backup files' stats, newest last; None when there is no backup folder (dev, tests)."""
    folder = Path(BACKUP_DIR)
    if not folder.is_dir():
        return None
    return sorted((f.stat() for f in folder.glob("data-*.db.gz")), key=lambda s: s.st_mtime)


def backup_age_hours(now=None):
    """Hours since the newest backup; None when there is no backup folder (dev, tests)."""
    files = backups()
    if files is None:
        return None
    if not files:
        return float("inf")
    return ((now or time.time()) - files[-1].st_mtime) / 3600


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
CERT_STATUS = {}       # target -> {"expires": epoch or None, "error": text or None}, for status()


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
            CERT_STATUS[target] = {"expires": None, "error": "unreachable"}
            continue
        if isinstance(expires, str):
            CERT_STATUS[target] = {"expires": None, "error": expires}
            _watch(f"cert_{port}", True, False, "Certificate problem", f"{target}: {expires}", "", "")
            continue
        CERT_STATUS[target] = {"expires": expires, "error": None}
        days = (expires - now) / 86400
        until = time.strftime("%Y-%m-%d", time.localtime(expires))
        _watch(f"cert_{port}", days < CERT_MIN_DAYS, days >= CERT_MIN_DAYS,
               "Certificate expires soon",
               f"{target} expires in {max(days, 0):.0f} days ({until}); is the renewal running?",
               "Certificate renewed", f"{target} is valid until {until}")


def _watch(name, bad, recovered, title, body, back_title, back_body):
    """One check: push when [bad] starts, and again once [recovered] (with hysteresis)."""
    excursion = Excursion(f"watchdog:{name}", "nodes", "server", {"event": "WATCHDOG", "device": name},
                          postpone_quiet=True)
    if bad:
        excursion.start(f"WATCHDOG_{name.upper()}", body, title, body)
    elif recovered:
        excursion.end(f"WATCHDOG_{name.upper()}_OK", back_body, back_title, back_body)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t is not None else None


def status(now=None):
    """What the watchdog sees, for GET /api/server_status (#260). "ok" uses the push thresholds
    (without the disk hysteresis); null where a check isn't configured or hasn't run yet."""
    now = now or time.time()
    files = backups()
    backup = None
    if files is not None:
        age = backup_age_hours(now)
        backup = {"last": _iso(files[-1].st_mtime) if files else None,
                  "size_bytes": files[-1].st_size if files else None,
                  "count": len(files), "ok": age <= BACKUP_MAX_AGE_HOURS}
    offsite = None
    age = offsite_age_hours(now)
    if age is not None:
        last = None if age == float("inf") else now - age * 3600
        offsite = {"last": _iso(last), "ok": age <= OFFSITE_MAX_AGE_HOURS}
    free = disk_free_mb()
    certs = []
    for target in CERT_CHECK:
        c = CERT_STATUS.get(target)
        expires = c and c["expires"]
        certs.append({"target": target, "expires": _iso(expires), "error": c and c["error"],
                      "ok": None if c is None else expires is not None and (expires - now) / 86400 >= CERT_MIN_DAYS})
    mqtt_ok = state.mqttc is not None and state.mqttc.is_connected()
    return {
        "backend": {"started": _iso(state.STARTED_AT), "mqtt_connected": mqtt_ok,
                    "last_message_age_s": int(now - state.LAST_MESSAGE_AT) if state.LAST_MESSAGE_AT else None},
        "backup": backup,
        "offsite": offsite,
        "disk": {"free_mb": int(free), "ok": free >= DISK_MIN_FREE_MB},
        "certs": certs,
    }


def _check_backup(now):
    age = backup_age_hours(now)
    if age is not None:
        _watch("backup", age > BACKUP_MAX_AGE_HOURS, age <= BACKUP_MAX_AGE_HOURS,
               "Backup failed", f"No database backup for over {BACKUP_MAX_AGE_HOURS} h (last: {_ago(age)})",
               "Backup OK again", "The nightly database backup ran again")


def _check_offsite(now):
    age = offsite_age_hours(now)
    if age is not None:
        _watch("offsite", age > OFFSITE_MAX_AGE_HOURS, age <= OFFSITE_MAX_AGE_HOURS,
               "Off-site backup failed", f"No backup uploaded to S3 for over {OFFSITE_MAX_AGE_HOURS} h (last: {_ago(age)})",
               "Off-site backup OK again", "The database backup reached S3 again")


def _check_disk(now):
    free = disk_free_mb()
    _watch("disk", free < DISK_MIN_FREE_MB, free >= DISK_MIN_FREE_MB + DISK_FREE_HYSTERESIS_MB,
           "Disk almost full", f"Only {free:.0f} MB free on the server",
           "Disk space OK again", f"{free:.0f} MB free on the server")


def check_watchdogs(now=None):
    """Each check on its own: one that raises (a backup file removed while it's stat-ed,
    disk_usage failing) doesn't skip the others (#303)."""
    for name, check in (("backup", _check_backup), ("offsite", _check_offsite),
                        ("disk", _check_disk), ("certificates", check_certs)):
        try:
            check(now)
        except Exception as e:
            log.warning("%s watchdog failed: %s", name, e)
