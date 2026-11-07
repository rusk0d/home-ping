"""Fridge temperature monitor alerts (#290, #294).

The node decides the alert state (contracts/mqtt.md, home/esp32/fridge_temp); this pushes
when it changes. system_state fridge_alert_state is the state last pushed (or muted), so a
failed push is retried on the next watchdog run instead of being lost. Each state has its
own cooldown key: a quick warm -> normal -> warm doesn't push again within the cooldown,
but warm_low -> warm_critical (a different key) pushes at once.
"""

import logging
import time

import alerts
import state
from config import NOTIFICATION_COOLDOWN
from store import db, get_state, set_state

log = logging.getLogger(__name__)

DEVICE = "fridge_temp_esp32"

MESSAGES = {
    "cold": ("Fridge too cold", "Below 0 °C for 30+ minutes"),
    "warm_low": ("Fridge warming up", "Above 5 °C for 30+ minutes"),
    "warm_critical": ("Fridge too warm", "Above 8 °C for 30+ minutes"),
    "warm_critical_duration": ("Fridge too warm (sustained)", "Above 5 °C for 2+ hours"),
}


def check_fridge_alerts(now=None):
    """Push when the newest fridge reading's alert state differs from the last one pushed."""
    now = time.time() if now is None else now
    with db() as conn:
        row = conn.execute(
            "SELECT alert FROM readings WHERE device = ? ORDER BY id DESC LIMIT 1", (DEVICE,)
        ).fetchone()
    if not row:
        return

    current = row[0] or ""
    last = get_state("fridge_alert_state", "")
    if current == last:
        return

    key = f"fridge:{current or 'normal'}"
    if now - state.LAST_NOTIFICATION.get(key, 0) <= NOTIFICATION_COOLDOWN:
        return   # state not saved: checked again on the next run

    if current:
        title, body = MESSAGES.get(current, ("Fridge alert", f"Alert: {current}"))
        ok = alerts.alert(DEVICE, "FRIDGE_ALERT", f"alert={current} (was: {last or 'normal'})",
                          title=title, body=body,
                          data={"event": "FRIDGE_ALERT", "device": DEVICE, "alert": current},
                          cooldown_key=key, kind="fridge")
    elif last in MESSAGES:
        ok = alerts.alert(DEVICE, "FRIDGE_ALERT_RECOVERY", f"back to normal (was: {last})",
                          title="Fridge back to normal", body="Temperature is within normal range",
                          data={"event": "FRIDGE_NORMAL", "device": DEVICE},
                          cooldown_key=key, kind="fridge")
    else:
        ok = True   # nothing to say (e.g. an unknown state cleared)

    if ok:
        set_state("fridge_alert_state", current)


def send_fridge_alerts():
    """For check_watchdogs(): never raises."""
    try:
        check_fridge_alerts()
    except Exception as e:
        log.error("Fridge alert check failed: %s", e)
