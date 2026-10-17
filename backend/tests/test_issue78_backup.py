"""#78: backup while a writer is busy, retention, restore
"""

from support import BACKEND, check


def test_issue78_backup(env):
    import sys, gzip, sqlite3, subprocess, threading
    from pathlib import Path
    backend = BACKEND

    con = sqlite3.connect("data.db")
    con.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, v REAL)")
    con.executemany("INSERT INTO readings (v) VALUES (?)", [(i,) for i in range(50000)])
    con.commit(); con.close()

    stop = False
    def writer():
        w = sqlite3.connect("data.db", timeout=10)
        while not stop:
            w.execute("INSERT INTO readings (v) VALUES (1)"); w.commit()
    t = threading.Thread(target=writer); t.start()
    r = subprocess.run([sys.executable, str(backend / "backup_db.py")], capture_output=True, text=True)
    stop = True; t.join()
    print(r.stdout.strip(), r.stderr.strip())
    check("backup ran while writing", r.returncode == 0)
    files = sorted(Path("backups").glob("data-*.db.gz"))
    check("one gz backup", len(files) == 1)
    Path("restored.db").write_bytes(gzip.decompress(files[0].read_bytes()))
    rc = sqlite3.connect("restored.db")
    check("restored copy is intact", rc.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    check("restored has the rows", rc.execute("SELECT COUNT(*) FROM readings").fetchone()[0] >= 50000)
    rc.close()
    check("no temp file left", not Path("backups/data.db.tmp").exists())

    for d in range(1, 11):
        Path(f"backups/data-2026-09-{d:02d}.db.gz").write_bytes(b"x")
    r = subprocess.run([sys.executable, str(backend / "backup_db.py"), "--keep", "7"], capture_output=True, text=True)
    left = sorted(p.name for p in Path("backups").glob("data-*.db.gz"))
    check("keeps the newest 7", len(left) == 7 and files[0].name in left and "data-2026-09-05.db.gz" in left and "data-2026-09-04.db.gz" not in left)

    r = subprocess.run([sys.executable, str(backend / "backup_db.py"), "--db", "missing.db"], capture_output=True, text=True)
    check("missing DB -> non-zero exit", r.returncode != 0 and "not found" in r.stderr)
