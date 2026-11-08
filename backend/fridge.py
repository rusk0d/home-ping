"""Fridge temperature monitor alerts (#290, #294, #300) and the door left open (#336).

The node decides the alert state (contracts/mqtt.md, home/esp32/fridge_temp); this pushes
when it changes. system_state:
- fridge_alert_state: the state last handled ("" = normal).
- fridge_alert_shown: "sent" / "muted" for that state's push, "" when there was none. A muted
  one (quiet hours, switch off) is pushed once that's over if the state still holds, and a
  "back to normal" only follows a sent one.
Both are saved only after the push went out, so a failed one is retried (#251 backoff).
- fridge_alert_urgent: "1" while the notification on the phone is a "too warm" one (URGENT).

Too warm (URGENT, #351) comes through quiet hours, and so does the "back to normal" that
replaces it; only the "fridge" switch mutes them. Too cold and warming up wait for the end of
quiet hours as above.

The node keeps its condition timers in RAM: after a reboot it reports "" (or a milder state)
until they've run again, up to 2 h. A step down while the node is younger than that and the
temperature is still outside the normal range is a reboot, not a recovery, and is held.
A step down between two alert states (cooling from warm_critical to warm_low) isn't pushed:
the more severe notification stays.
"""

import logging
import time
from datetime import timedelta

import alerts
import state
from config import (
    FRIDGE_DEVICE, FRIDGE_DOOR_CLOSED_C, FRIDGE_DOOR_OPEN_MINUTES, FRIDGE_DOOR_RISE_C,
    FRIDGE_DOOR_RISE_MINUTES, NOTIFICATION_COOLDOWN,
)
from store import db, get_state, parse_ts, set_state, utcnow

log = logging.getLogger(__name__)

DEVICE = FRIDGE_DEVICE
EVENT = "FRIDGE_ALERT"   # alert and recovery share it: one notification slot on the phone

MESSAGES = {
    "cold": ("Fridge too cold", "Below 0 °C for 30+ minutes"),
    "warm_low": ("Fridge warming up", "Above 5 °C for 30+ minutes"),
    "warm_critical": ("Fridge too warm", "Above 8 °C for 30+ minutes"),
    "warm_critical_duration": ("Fridge too warm (sustained)", "Above 5 °C for 2+ hours"),
}
URGENT = ("warm_critical", "warm_critical_duration")
SEVERITY = {"": 0, "cold": 1, "warm_low": 1, "warm_critical": 2, "warm_critical_duration": 2}

# Mirrors the firmware (fridge_node.ino): its longest timer, and the range where no condition
# can hold (thresholds 0 / 5 °C, 0.5 °C hysteresis)
NODE_TIMERS_S = 2 * 60 * 60
NORMAL_RANGE = (0.5, 4.5)


def _push(current, last, urgent):
    """Push the alert for [current] ("" = back to normal, after [last]); [urgent]: through
    quiet hours."""
    if current:
        title, body = MESSAGES.get(current, ("Fridge alert", f"Alert: {current}"))
        detail = f"alert={current} (was: {last or 'normal'})"
    else:
        title, body = "Fridge back to normal", "Temperature is within normal range"
        detail = f"back to normal (was: {last})"
    return alerts.alert(DEVICE, "FRIDGE_ALERT" if current else "FRIDGE_ALERT_RECOVERY", detail,
                        title=title, body=body,
                        data={"event": EVENT, "device": DEVICE, "alert": current},
                        cooldown_key=f"fridge:{current or 'normal'}", kind="fridge",
                        ignore_quiet=urgent)


def _save(current, shown, urgent=None):
    """[urgent]: whether a "too warm" notification is on the phone now; None leaves it."""
    set_state("fridge_alert_state", current)
    set_state("fridge_alert_shown", shown)
    if urgent is not None:
        set_state("fridge_alert_urgent", "1" if urgent else "")


def check_fridge_alerts(now=None):
    """Push when the newest fridge reading's alert state differs from the one handled."""
    now = time.time() if now is None else now
    with db() as conn:
        row = conn.execute(
            "SELECT alert, temp, uptime FROM readings WHERE device = ? ORDER BY id DESC LIMIT 1",
            (DEVICE,)).fetchone()
    if not row:
        return
    current, temp, uptime = (row[0] or ""), row[1], row[2]
    last = get_state("fridge_alert_state", "") or ""
    shown = get_state("fridge_alert_shown", "") or ""

    if current == last:
        # A muted alert that still holds: push it once it isn't muted any more
        urgent = current in URGENT
        if current and shown == "muted" and alerts.alert_muted("fridge", urgent) is None:
            if _push(current, last, urgent) == "sent":
                _save(current, "sent", urgent)
        return

    milder = SEVERITY.get(current, 1) < SEVERITY.get(last, 1)
    if milder and uptime is not None and uptime < NODE_TIMERS_S and not (
            temp is not None and NORMAL_RANGE[0] <= temp <= NORMAL_RANGE[1]):
        return   # rebooted node, timers not run yet: hold the last state

    if now - state.LAST_NOTIFICATION.get(f"fridge:{current or 'normal'}", 0) <= NOTIFICATION_COOLDOWN:
        return   # not saved: checked again on the next run

    if not current:
        if shown == "sent" and not _push("", last, get_state("fridge_alert_urgent", "") == "1"):
            return
        _save("", "", False)
    elif milder and shown:
        _save(current, shown)   # cooling down between alerts: keep the more severe notification
    else:
        result = _push(current, last, current in URGENT)
        if result:
            _save(current, result, result == "sent" and current in URGENT)


# ---------- door left open (#336) ----------
# On every fridge reading (ingest), not in the 5-minute loop: the push should come about
# FRIDGE_DOOR_OPEN_MINUTES after the door opened, not up to 5 min later. The node decides the
# temperature alerts above; this one is the backend's, from the readings.
# system_state: fridge_door_rise = "<start ts>|<base °C>" while a fast rise is being watched;
# fridge_door = the Excursion state of its push ("sent" / "muted", "" when none).

DOOR = alerts.Excursion("fridge_door", "fridge", DEVICE, {"event": "FRIDGE_DOOR", "device": DEVICE})


def door_step(rise, recent):
    """One reading through the door rule; also used by tools/fridge_door_replay.py.

    [rise]: (start datetime, base °C) of the fast rise being watched, or None. [recent]: the
    fridge's (datetime, °C) readings of the last FRIDGE_DOOR_RISE_MINUTES or more, oldest
    first, the newest last. Returns (rise, event): event "open" while the door looks left
    open (every reading: the push itself is once), "closed" when a watched rise is over.
    """
    ts, temp = recent[-1]
    if rise is None:
        window = [r for r in recent if r[0] >= ts - timedelta(minutes=FRIDGE_DOOR_RISE_MINUTES)]
        # The newest of the lowest readings: the last one before the door opened, so the
        # minutes count from the opening, not from a steady stretch before it
        start, base = min(window, key=lambda r: (r[1], -r[0].timestamp()))
        if temp - base < FRIDGE_DOOR_RISE_C:
            return None, None
        rise = (start, base)
    start, base = rise
    if temp <= base + FRIDGE_DOOR_CLOSED_C:
        return None, "closed"
    return rise, "open" if ts - start >= timedelta(minutes=FRIDGE_DOOR_OPEN_MINUTES) else None


def door_state():
    """{"door_open", "door_open_since"} for /api/latest_all (#361): open from the push (sent or
    muted, so also in quiet hours) until the rise is over; since = the start of the rise."""
    if DOOR.current()[0] is None:
        return {"door_open": False, "door_open_since": None}
    start = (get_state("fridge_door_rise", "") or "").partition("|")[0]
    return {"door_open": True, "door_open_since": start.replace(" ", "T") + "Z" if start else None}


def check_door(now=None):
    """The door rule on the newest fridge reading; pushes "Fridge door open?" once per rise and
    "Fridge door closed" after a sent one."""
    since = (now or utcnow()) - timedelta(minutes=FRIDGE_DOOR_RISE_MINUTES + 1)
    with db() as conn:
        rows = conn.execute(
            "SELECT ts, temp FROM readings WHERE device = ? AND temp IS NOT NULL AND ts >= ? ORDER BY ts",
            (DEVICE, since.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    if not rows:
        return None
    recent = [(parse_ts(ts), temp) for ts, temp in rows]
    saved = get_state("fridge_door_rise", "") or ""
    start, _, base = saved.partition("|")
    rise = (parse_ts(start), float(base)) if saved else None

    rise, event = door_step(rise, recent)
    new = f"{rise[0].strftime('%Y-%m-%d %H:%M:%S')}|{rise[1]}" if rise else ""
    if new != saved:
        set_state("fridge_door_rise", new)
    ts, temp = recent[-1]
    if event == "open":
        minutes = round((ts - rise[0]).total_seconds() / 60)
        DOOR.start("FRIDGE_DOOR_OPEN", f"temp={temp} (was {rise[1]} {minutes} min ago)",
                   "Fridge door open?", f"Up from {rise[1]:.1f} to {temp:.1f} °C in {minutes} min and not cooling down")
    elif event == "closed":
        DOOR.end("FRIDGE_DOOR_CLOSED", f"temp={temp}", "Fridge door closed", f"Back to {temp:.1f} °C")
    return event
