"""Push notifications (FCM), cooldowns and the notification settings (#82).
"""

import logging
import time
from datetime import datetime
from firebase_admin import messaging

import state
from config import NOTIFICATION_COOLDOWN, NOTIFY_KINDS, PUSH_RETRY_SECONDS, QUIET_DEFAULTS
from store import get_state, log_event, set_state

log = logging.getLogger(__name__)


# -------------------------
# PUSH NOTIFICATIONS (FCM)
# -------------------------
def send_push(title, body, data=None):
    """Send a data-only push to topic "env". Returns True if FCM accepted it.

    Data-only so the Android app always builds the notification itself (its own
    channels and IDs), also in the background; title/body travel in the data map.
    High priority so it is delivered while the phone is in Doze.
    """
    log.debug("Sending push: title=%s, body=%s, data=%s", title, body, data)
    try:
        message = messaging.Message(
            data={"title": title, "body": body, **(data or {})},
            android=messaging.AndroidConfig(priority="high"),
            topic="env"
        )
        response = messaging.send(message)
        log.info("Push sent: %s: %s (%s)", title, body, response)
        return True
    except Exception as e:
        log.error("Push failed: %s: %s", title, e)
        return False


def alert(device, event_type, detail, title, body, data, cooldown_key=None, kind=None):
    """Send a push and record it in events as "sent, ...", "failed, ..." or "muted (...), ...".

    The cooldown only starts after a successful send; a failed one is retried
    after PUSH_RETRY_SECONDS instead of being silenced for the whole cooldown.
    [kind] (one of NOTIFY_KINDS) can be switched off or muted by quiet hours (#82);
    a muted alert is handled like a sent one (cooldown, once per outage) without a push.
    Returns "sent", "muted", or False when the send failed.
    """
    muted = alert_muted(kind)
    if muted:
        log.info("%s %s muted (%s): %s", device, event_type, muted, detail)
        log_event(device, event_type, f"muted ({muted}), {detail}")
        if cooldown_key:
            set_cooldown(cooldown_key, time.time())
        return "muted"
    ok = send_push(title, body, data)
    status = "sent" if ok else "failed"
    log_event(device, event_type, f"{status}, {detail}")
    if cooldown_key:
        now = time.time()
        set_cooldown(cooldown_key, now if ok else now - NOTIFICATION_COOLDOWN + PUSH_RETRY_SECONDS)
    return "sent" if ok else False


# -------------------------
# NOTIFICATION SETTINGS (#82)
# -------------------------
# system_state: notify:<kind> = '1'/'0' (missing = on), quiet_hours = '1'/'0',
# quiet_start / quiet_end = "HH:MM" in the server's local time (Budapest).
# Occupancy alerts have their own switch (occupancy_armed) and ignore quiet hours.


def notification_settings():
    return {
        **{k: get_state(f"notify:{k}", "1") == "1" for k in NOTIFY_KINDS},
        "quiet_hours": {
            "enabled": get_state("quiet_hours", "0") == "1",
            "start": get_state("quiet_start", QUIET_DEFAULTS["start"]),
            "end": get_state("quiet_end", QUIET_DEFAULTS["end"]),
        },
    }


def in_quiet_hours(now=None):
    """True inside the quiet-hours window; it may cross midnight (22:00-07:00)."""
    if get_state("quiet_hours", "0") != "1":
        return False
    start = get_state("quiet_start", QUIET_DEFAULTS["start"])
    end = get_state("quiet_end", QUIET_DEFAULTS["end"])
    t = (now or datetime.now()).strftime("%H:%M")   # local time, like the stored window
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def alert_muted(kind):
    """Why an alert of [kind] gets no push ("off", "quiet hours"), or None."""
    if kind is None:
        return None
    if get_state(f"notify:{kind}", "1") != "1":
        return "off"
    if in_quiet_hours():
        return "quiet hours"
    return None


def set_cooldown(key, started):
    state.LAST_NOTIFICATION[key] = started
    set_state(f"cooldown:{key}", repr(started))


def set_radar_alerted(device, alerted):
    if alerted:
        state.RADAR_ALERTED.add(device)
    else:
        state.RADAR_ALERTED.discard(device)
    set_state(f"radar_alerted:{device}", "1" if alerted else "0")
