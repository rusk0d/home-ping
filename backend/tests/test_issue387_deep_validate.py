"""#387: validate_db_deep.py counts only sent pushes in the cooldown check (a failed push is
retried after a minute, a muted one is logged too) and checks the fridge's hourly columns."""

import sys

from support import BACKEND, check

TOOLS = str(BACKEND / "tools")


def _deep(env, capsys):
    """Run the deep checks against the test's data.db: (exit code, output)."""
    sys.path.insert(0, TOOLS)
    try:
        import _common
        _common.DB_PATH = env / "data.db"   # connect_ro() reads it at call time
        import validate_db_deep
        code = validate_db_deep.run_deep_checks()
    finally:
        sys.path.remove(TOOLS)
        for name in ("_common", "validate_db_deep"):
            sys.modules.pop(name, None)
    return code, capsys.readouterr().out


def test_issue387_deep_validate(env, capsys):
    import server
    from store import db
    server.prepare()

    def event(ts, value, type_="TEMP_ALERT"):
        with db() as conn:
            conn.execute("INSERT INTO events (device, ts, type, value) VALUES ('home_ping_esp32', ?, ?, ?)",
                         (ts, type_, value))

    # A push that failed and went out on the retry a minute later, and a muted one after it
    event("2026-11-01 10:00:00", "failed, temp 28.1")
    event("2026-11-01 10:01:00", "sent, temp 28.2")
    event("2026-11-01 10:05:00", "muted (quiet hours), temp 28.3")
    code, out = _deep(env, capsys)
    check(f"retry and muted: no cooldown violation ({out[-400:]})", "No alert cooldown violations" in out)

    # Two sent within the cooldown: still a violation
    event("2026-11-01 10:06:00", "sent, temp 28.4")
    code, out = _deep(env, capsys)
    check("two sent 5 min apart: a violation", "Alert cooldown violation" in out and code == 1)

    # The fridge's columns are checked too (#331)
    with db() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("INSERT INTO hourly (hour, fridge_avg, fridge_min, fridge_max) VALUES ('2026-11-01 10:00:00', 4.0, 5.0, 6.0)")
    code, out = _deep(env, capsys)
    check(f"fridge min > avg: found ({out[-400:]})", "1 fridge violations" in out and code == 1)
