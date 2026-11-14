"""#422: the flat's occupied / vacant changes (FLAT_OCCUPANCY) instead of every flip of a radar's
raw flag (OCCUPANCY_CHANGE): occupied at once, vacant after FLAT_VACANT_MINUTES without combined
occupancy; a flicker in between logs nothing."""

import sys
import time
import types

from support import BACKEND, check


def test_issue422_flat_occupancy(env, capsys):
    import server
    import ingest, occupancy, state
    from config import FLAT_VACANT_MINUTES
    from store import db, get_state, set_state
    server.prepare()

    hold = FLAT_VACANT_MINUTES * 60

    def reading(device, moving):
        ingest.handle_presence_data({"device": device, "presence": moving, "moving": moving, "stationary": False,
                                     "signal": 0, "distance": 100, "rssi": -60, "uptime": 1000,
                                     "radar_connected": True})

    def events(type_="FLAT_OCCUPANCY"):
        with db() as conn:
            return [r[0] for r in conn.execute("SELECT value FROM events WHERE type = ? ORDER BY id", (type_,))]

    # --- through the presence handler: the raw flips aren't logged any more
    for moving in (True, False, True, False, True, True):
        reading("home_ping_esp32", moving)
    check(f"no OCCUPANCY_CHANGE ({events('OCCUPANCY_CHANGE')})", events("OCCUPANCY_CHANGE") == [])
    check(f"occupied once ({events()})", events() == ["occupied"] and get_state("flat_occupied") == "1")

    # --- vacant only after the hold; a flicker in between changes nothing. A fake clock for
    # occupancy's hold (the readings themselves are stored at the real now)
    clock = [time.time()]
    occupancy.time = types.SimpleNamespace(time=lambda: clock[0])

    def at(seconds, device, *moving):
        clock[0] += seconds
        for m in moving:
            reading(device, m)

    at(0, "home_ping_esp32", True)
    # The first of these still counts as occupied (2 of the last 3), so the hold starts there
    at(60, "home_ping_esp32", False, False, False)
    check("vacant 1 min: nothing yet", events() == ["occupied"])
    at(hold - 1, "home_ping_esp32", False)
    check("just before the hold: nothing", events() == ["occupied"])
    at(1, "home_ping_esp32", False)
    check(f"after the hold: vacant ({events()})", events() == ["occupied", f"vacant (no one for {FLAT_VACANT_MINUTES} min)"]
          and get_state("flat_occupied") == "0")
    at(600, "home_ping_esp32", False)
    check("vacant again: nothing", len(events()) == 2)

    at(60, "hallway_presence_esp32", True, True)
    check(f"someone back: occupied ({events()})", events()[-1] == "occupied" and len(events()) == 3)
    at(60, "hallway_presence_esp32", False, False)
    at(300, "hallway_presence_esp32", False)
    at(60, "hallway_presence_esp32", True, True)
    at(hold - 60, "hallway_presence_esp32", False, False, False)
    check(f"a 6-min gap, then {FLAT_VACANT_MINUTES - 1} min: nothing ({events()})", len(events()) == 3)

    # --- after a restart the hold starts over (the last occupied time was in memory)
    state.FLAT.clear()
    at(hold, "hallway_presence_esp32", False)
    check("restart, vacant: not at once", len(events()) == 3 and get_state("flat_occupied") == "1")
    at(hold, "hallway_presence_esp32", False)
    check(f"restart, vacant: after the hold ({events()})", events()[-1].startswith("vacant") and len(events()) == 4)

    # --- no radar in the last 2 min: unknown, nothing changes
    with db() as conn:
        conn.execute("UPDATE presence_readings SET ts = datetime(ts, '-10 minutes')")
    set_state("flat_occupied", "1")
    check("no radar data: nothing", occupancy.note_flat_occupancy() is None
          and get_state("flat_occupied") == "1")

    # --- validate_db_deep check 8: the events alternate
    tools = str(BACKEND / "tools")
    sys.path.insert(0, tools)
    try:
        import _common
        _common.DB_PATH = env / "data.db"
        import validate_db_deep
        validate_db_deep.run_deep_checks()
        out = capsys.readouterr().out
        check(f"deep check 8: alternate ({out[-300:]})", "FLAT_OCCUPANCY events alternate" in out)
        with db() as conn:
            conn.execute("INSERT INTO events (device, ts, type, value) VALUES ('system', datetime('now'), 'FLAT_OCCUPANCY', 'vacant')")
        validate_db_deep.run_deep_checks()
        out = capsys.readouterr().out
        check(f"deep check 8: vacant twice found ({out[-300:]})", "FLAT_OCCUPANCY: vacant twice" in out)
    finally:
        sys.path.remove(tools)
        for name in ("_common", "validate_db_deep"):
            sys.modules.pop(name, None)
