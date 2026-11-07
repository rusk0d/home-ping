"""Fridge temperature monitor alerts (#290, #294, #300).

The node decides the alert state (contracts/mqtt.md, home/esp32/fridge_temp); this pushes
when it changes. system_state:
- fridge_alert_state: the state last handled ("" = normal).
- fridge_alert_shown: "sent" / "muted" for that state's push, "" when there was none. A muted
  one (quiet hours, switch off) is pushed once that's over if the state still holds, and a
  "back to normal" only follows a sent one.
Both are saved only after the push went out, so a failed one is retried (#251 backoff).

The node keeps its condition timers in RAM: after a reboot it reports "" (or a milder state)
until they've run again, up to 2 h. A step down while the node is younger than that and the
temperature is still outside the normal range is a reboot, not a recovery, and is held.
A step down between two alert states (cooling from warm_critical to warm_low) isn't pushed:
the more severe notification stays.
"""

import logging
import time

import alerts
import state
from config import NOTIFICATION_COOLDOWN
from store import db, get_state, set_state

log = logging.getLogger(__name__)

DEVICE = "fridge_temp_esp32"
EVENT = "FRIDGE_ALERT"   # alert and recovery share it: one notification slot on the phone

MESSAGES = {
    "cold": ("Fridge too cold", "Below 0 °C for 30+ minutes"),
    "warm_low": ("Fridge warming up", "Above 5 °C for 30+ minutes"),
    "warm_critical": ("Fridge too warm", "Above 8 °C for 30+ minutes"),
    "warm_critical_duration": ("Fridge too warm (sustained)", "Above 5 °C for 2+ hours"),
}
SEVERITY = {"": 0, "cold": 1, "warm_low": 1, "warm_critical": 2, "warm_critical_duration": 2}

# Mirrors the firmware (fridge_node.ino): its longest timer, and the range where no condition
# can hold (thresholds 0 / 5 °C, 0.5 °C hysteresis)
NODE_TIMERS_S = 2 * 60 * 60
NORMAL_RANGE = (0.5, 4.5)


def _push(current, last):
    """Push the alert for [current] ("" = back to normal, after [last])."""
    if current:
        title, body = MESSAGES.get(current, ("Fridge alert", f"Alert: {current}"))
        detail = f"alert={current} (was: {last or 'normal'})"
    else:
        title, body = "Fridge back to normal", "Temperature is within normal range"
        detail = f"back to normal (was: {last})"
    return alerts.alert(DEVICE, "FRIDGE_ALERT" if current else "FRIDGE_ALERT_RECOVERY", detail,
                        title=title, body=body,
                        data={"event": EVENT, "device": DEVICE, "alert": current},
                        cooldown_key=f"fridge:{current or 'normal'}", kind="fridge")


def _save(current, shown):
    set_state("fridge_alert_state", current)
    set_state("fridge_alert_shown", shown)


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
        if current and shown == "muted" and alerts.alert_muted("fridge") is None:
            if _push(current, last) == "sent":
                _save(current, "sent")
        return

    milder = SEVERITY.get(current, 1) < SEVERITY.get(last, 1)
    if milder and uptime is not None and uptime < NODE_TIMERS_S and not (
            temp is not None and NORMAL_RANGE[0] <= temp <= NORMAL_RANGE[1]):
        return   # rebooted node, timers not run yet: hold the last state

    if now - state.LAST_NOTIFICATION.get(f"fridge:{current or 'normal'}", 0) <= NOTIFICATION_COOLDOWN:
        return   # not saved: checked again on the next run

    if not current:
        if shown == "sent" and not _push("", last):
            return
        _save("", "")
    elif milder and shown:
        _save(current, shown)   # cooling down between alerts: keep the more severe notification
    else:
        result = _push(current, last)
        if result:
            _save(current, result)
