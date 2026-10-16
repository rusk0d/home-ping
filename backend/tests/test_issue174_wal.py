"""#174: the database runs in WAL mode; the nightly backup still works with it.
"""

from support import BACKEND, check


def test_issue174_wal(env):
    import gzip, sqlite3, subprocess, sys
    from pathlib import Path
    import server, config, store
    server.prepare()

    con = sqlite3.connect(config.DB)
    check("journal_mode is WAL after init_db", con.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    con.close()
    with store.db() as conn:
        check("db(): synchronous=NORMAL", conn.execute("PRAGMA synchronous").fetchone()[0] == 1)
        conn.execute("INSERT INTO readings (device, ts, temp) VALUES ('d', '2026-10-17 00:00:00', 21.5)")

    # A reader sees committed data while another connection has an open write transaction
    w = sqlite3.connect(config.DB, isolation_level=None)
    w.execute("BEGIN IMMEDIATE")
    w.execute("INSERT INTO readings (device, ts, temp) VALUES ('d', '2026-10-17 00:01:00', 99)")
    with store.db() as conn:
        check("reader not blocked, sees only committed rows",
              conn.execute("SELECT COUNT(*), MAX(temp) FROM readings").fetchone() == (1, 21.5))

    # Backup while that write transaction is still open, and once all connections are closed
    for label in ("writer open", "all closed"):
        r = subprocess.run([sys.executable, str(BACKEND / "backup_db.py")], capture_output=True, text=True)
        check(f"backup ({label}) ran: {r.stderr.strip()}", r.returncode == 0)
        if label == "writer open":
            w.execute("COMMIT"); w.close()
    backup = sorted(Path("backups").glob("data-*.db.gz"))[-1]
    Path("restored.db").write_bytes(gzip.decompress(backup.read_bytes()))
    rc = sqlite3.connect("restored.db")
    check("restored copy is intact", rc.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    check("restored copy has both rows", rc.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2)
    rc.close()
