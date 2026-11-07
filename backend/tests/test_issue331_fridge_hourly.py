"""#331: the fridge's own columns in `hourly` (never in the room ones, #290), the one-time
backfill of the hours already rolled up, and the fridge fields of /api/hourly and /api/daily."""
import base64
from datetime import timedelta

from support import check


def test_issue331_fridge_hourly(env):
    import server
    import api, config, store
    from store import db, utcnow
    server.prepare()
    fridge, room = config.FRIDGE_DEVICE, "home_ping_esp32"

    def add(device, ts, temp):
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp) VALUES (?, ?, ?)",
                         (device, ts.strftime("%Y-%m-%d %H:%M:%S"), temp))

    def hourly_row(hour):
        with db() as conn:
            r = conn.execute("SELECT temp_avg, fridge_avg, fridge_min, fridge_max FROM hourly WHERE hour = ?",
                             (hour.strftime("%Y-%m-%d %H:00:00"),)).fetchone()
        return tuple(r) if r else None

    now = utcnow().replace(minute=0, second=0, microsecond=0)
    h3, h2 = now - timedelta(hours=3), now - timedelta(hours=2)

    # --- rollup: room and fridge apart; an hour with only the fridge still gets a row
    add(room, h3 + timedelta(minutes=5), 21.0)
    add(fridge, h3 + timedelta(minutes=5), 3.0)
    add(fridge, h3 + timedelta(minutes=35), 5.0)
    add(fridge, h2 + timedelta(minutes=10), 4.0)
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key = 'hourly_until'")
    store.rollup_hourly()
    check(f"room hour ({hourly_row(h3)})", hourly_row(h3) == (21.0, 4.0, 3.0, 5.0))
    check(f"fridge-only hour ({hourly_row(h2)})", hourly_row(h2) == (None, 4.0, 4.0, 4.0))

    # --- migration 5: fills the fridge columns of rolled-up hours, leaves the room ones
    with db() as conn:
        conn.execute("UPDATE hourly SET fridge_avg = NULL, fridge_min = NULL, fridge_max = NULL")
        conn.execute("UPDATE hourly SET temp_avg = 99 WHERE hour = ?", (h3.strftime("%Y-%m-%d %H:00:00"),))
        conn.execute("DELETE FROM hourly WHERE hour = ?", (h2.strftime("%Y-%m-%d %H:00:00"),))
    store.set_state("schema_version", "4")
    store.init_db()
    check(f"backfilled, room kept ({hourly_row(h3)})", hourly_row(h3) == (99.0, 4.0, 3.0, 5.0))
    check(f"missing hour added ({hourly_row(h2)})", hourly_row(h2) == (None, 4.0, 4.0, 4.0))
    check("schema_version 5", store.get_state("schema_version") == "5")
    # a database that never rolled up: nothing to backfill, no error
    with db() as conn:
        conn.execute("DELETE FROM system_state WHERE key = 'hourly_until'")
    store.set_state("schema_version", "4")
    store.init_db()
    check("no hourly_until: migration is a no-op", store.get_state("schema_version") == "5")

    # --- the endpoints
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = api.app.test_client()
    add(fridge, utcnow() - timedelta(minutes=1), 6.5)
    for path in ("/api/hourly?days=2", "/api/daily?days=2"):
        rows = client.get(path, headers=auth).get_json()
        check(f"{path}: fridge fields", rows and all({"avg_fridge", "min_fridge", "max_fridge"} <= set(r) for r in rows))
    hourly = client.get("/api/hourly?days=2", headers=auth).get_json()
    check("today's hour from the raw fridge readings",
          any(r["max_fridge"] == 6.5 and r["avg_temp"] is None for r in hourly))
    check("room temps never include the fridge", all(r["avg_temp"] in (None, 21.0, 99.0) for r in hourly))
    daily = client.get("/api/daily?days=2", headers=auth).get_json()
    check(f"daily min/max ({[(d['min_fridge'], d['max_fridge']) for d in daily]})",
          min(d["min_fridge"] for d in daily if d["min_fridge"] is not None) == 3.0)
