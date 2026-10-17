"""#176: watchdog pushes for an overdue backup and low disk space.
"""

from support import check


def test_issue176_watchdog(env):
    import os, sqlite3, time
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
    def backup_file(name, hours_old):
        f = backups / name
        f.write_bytes(b"x")
        t = time.time() - hours_old * 3600
        os.utime(f, (t, t))

    # No backup folder (dev, tests): no backup check; the disk has room
    server_checks.check_watchdogs()
    check("no folder: no push", pushes() == [])

    # Folder without backups: overdue, but postponed during quiet hours
    backups.mkdir()
    store.set_state("quiet_hours", "1"); store.set_state("quiet_start", "00:00"); store.set_state("quiet_end", "23:59")
    server_checks.check_watchdogs()
    check("quiet hours: postponed, not dropped", pushes() == [] and store.get_state("watchdog:backup") == "0")
    store.set_state("quiet_hours", "0")
    server_checks.check_watchdogs()
    check("after quiet hours: 'Backup failed' push", pushes() == [("backup", "Backup failed")])
    check("pushed flag stored", store.get_state("watchdog:backup") == "1")
    server_checks.check_watchdogs()
    check("once per excursion: no repeat", pushes() == [])

    # A fresh backup ends it, with a push in the same notification slot
    backup_file("data-2026-10-17.db.gz", 1)
    server_checks.check_watchdogs()
    check("fresh backup: 'Backup OK again'", pushes() == [("backup", "Backup OK again")])
    check("flag cleared", store.get_state("watchdog:backup") == "0")

    # Too old again, but the "nodes" switch is off: no push, and no "OK" push later
    backup_file("data-2026-10-17.db.gz", config.BACKUP_MAX_AGE_HOURS + 1)
    store.set_state("notify:nodes", "0")
    server_checks.check_watchdogs()
    check("switch off: no push, muted flag", pushes() == [] and store.get_state("watchdog:backup") == "m")
    store.set_state("notify:nodes", "1")
    backup_file("data-2026-10-18.db.gz", 0)
    server_checks.check_watchdogs()
    check("muted excursion ends without an 'OK' push", pushes() == [] and store.get_state("watchdog:backup") == "0")

    # A failed send is retried at the next check
    os.remove(backups / "data-2026-10-17.db.gz"); os.remove(backups / "data-2026-10-18.db.gz")
    m.send.side_effect = Exception("FCM down")
    server_checks.check_watchdogs()
    m.send.side_effect = None
    check("failed send: flag not set", store.get_state("watchdog:backup") == "0")
    m.Message.reset_mock()
    server_checks.check_watchdogs()
    check("...retried next time", pushes() == [("backup", "Backup failed")])
    con = sqlite3.connect(config.DB)
    body = [r[0] for r in con.execute("SELECT value FROM events WHERE type='WATCHDOG_BACKUP'")]
    con.close()
    check("event logged with 'last: never'", any("last: never" in b for b in body))

    # Disk: below the minimum, then back above min + hysteresis
    free = server_checks.disk_free_mb()
    server_checks.DISK_MIN_FREE_MB = free + 1000
    server_checks.check_watchdogs()
    check("low disk: 'Disk almost full'", pushes() == [("disk", "Disk almost full")])
    server_checks.DISK_MIN_FREE_MB = free - config.DISK_FREE_HYSTERESIS_MB / 2
    server_checks.check_watchdogs()
    check("hysteresis: not 'OK' just above the minimum", pushes() == [])
    server_checks.DISK_MIN_FREE_MB = free - config.DISK_FREE_HYSTERESIS_MB - 100
    server_checks.check_watchdogs()
    check("disk OK again", pushes() == [("disk", "Disk space OK again")])
