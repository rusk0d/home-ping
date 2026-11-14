"""Shared by the backend tests (#124)."""
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent   # backend/


def check(label, cond):
    """One named check: fails the test with [label] as the message."""
    assert cond, label


def quiet_now(on=True):
    """Quiet hours on (or off, [on] False) with a window from an hour before now to an hour
    after (local time, like the stored window), so "now" is inside it at any time of day. A
    fixed 00:00-23:59 isn't quiet during 23:59, as the window's end is exclusive (#424)."""
    from datetime import datetime, timedelta
    from store import set_state
    now = datetime.now()
    set_state("quiet_hours", "1" if on else "0")
    set_state("quiet_start", (now - timedelta(hours=1)).strftime("%H:%M"))
    set_state("quiet_end", (now + timedelta(hours=1)).strftime("%H:%M"))


def add_devices(*ids):
    """Make [ids] known room devices for this test: the backend drops messages from a device
    that isn't in config.DEVICES (#462). config is fresh in every test, so nothing leaks."""
    import config
    for device in ids:
        config.DEVICES.setdefault(device, {"name": device, "role": "room"})
