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


def alert(device, event_type, detail, title, body, data, cooldown_key=None, kind=None, ignore_quiet=False):
    """Send a push and record it in events as "sent, ...", "failed, ..." or "muted (...), ...".

    The cooldown only starts after a successful send; a failed one is retried
    after PUSH_RETRY_SECONDS (longer each time in a row, #251) instead of being
    silenced for the whole cooldown.
    [kind] (one of NOTIFY_KINDS) can be switched off or muted by quiet hours (#82);
    a muted alert is handled like a sent one (cooldown, once per outage) without a push.
    [ignore_quiet]: only the switch can mute it, not quiet hours (a fridge too warm, #351).
    Returns "sent", "muted", or False when the send failed.
    """
    muted = alert_muted(kind, ignore_quiet)
    if muted:
        log.info("%s %s muted (%s): %s", device, event_type, muted, detail)
        log_event(device, event_type, f"muted ({muted}), {detail}")
        if cooldown_key:
            state.PUSH_FAILURES.pop(cooldown_key, None)
            set_cooldown(cooldown_key, time.time())
        return "muted"
    ok = send_push(title, body, data)
    status = "sent" if ok else "failed"
    log_event(device, event_type, f"{status}, {detail}")
    if cooldown_key:
        now = time.time()
        if ok:
            state.PUSH_FAILURES.pop(cooldown_key, None)
            set_cooldown(cooldown_key, now)
        else:
            # Back-dated cooldown start, so the usual cooldown check lets it through again
            # after the delay
            delay = retry_delay(cooldown_key)
            log.info("%s %s: push failed, retry in %d s", device, event_type, delay)
            set_cooldown(cooldown_key, now - NOTIFICATION_COOLDOWN + delay)
    return "sent" if ok else False


class Excursion:
    """A condition that starts and ends, pushed once per excursion (#305).

    State in system_state [key]: "" while there's none, else how its start went, "sent" or
    "muted", with ":<band>" when the condition has bands (humidity "sent:high"). Saved only
    once the push went out: a failed one is tried again at the next check, after the back-off
    of [cooldown_key] when there is one (#251). Another band while one is on is a new
    excursion (humidity high -> low). The end pushes only when the start was sent: it shares
    data["event"] / data["device"] with it, so it replaces it on the phone; after a muted
    start it's only logged.
    [cooldown_key]: also at most one start push per NOTIFICATION_COOLDOWN.
    [postpone_quiet]: quiet hours hold the start back until they're over instead of muting
    it (the server watchdogs: nothing urgent, but it mustn't get lost).
    """

    def __init__(self, key, kind, device, data, cooldown_key=None, postpone_quiet=False):
        self.key, self.kind, self.device, self.data = key, kind, device, data
        self.cooldown_key, self.postpone_quiet = cooldown_key, postpone_quiet

    def current(self):
        """(status, band): ("sent" / "muted", band or None), or (None, None) when none is on."""
        status, _, band = (get_state(self.key, "") or "").partition(":")
        return (status, band or None) if status in ("sent", "muted") else (None, None)

    def start(self, event_type, detail, title, body, band=None, data=None):
        """The condition holds: push it unless this excursion (and band) was handled already.
        Returns "sent" / "muted" when it was handled now, else None."""
        status, current = self.current()
        if status and current == band:
            return None
        if self.cooldown_key and (time.time() - state.LAST_NOTIFICATION.get(self.cooldown_key, 0)
                                  <= NOTIFICATION_COOLDOWN):
            return None
        if self.postpone_quiet and alert_muted(self.kind) == "quiet hours":
            return None
        result = alert(self.device, event_type, detail, title, body, {**self.data, **(data or {})},
                       cooldown_key=self.cooldown_key, kind=self.kind)
        if result:
            set_state(self.key, result + (f":{band}" if band else ""))
        return result or None

    def end(self, event_type=None, detail="", title=None, body=None, data=None):
        """The condition is over. Pushes [title] / [body] when the start was sent; without an
        [event_type] it just ends (no recovery push). Returns whether one was on."""
        status, _ = self.current()
        if not status:
            return False
        set_state(self.key, "")
        if event_type is None:
            return True
        if status == "sent":
            alert(self.device, event_type, detail, title, body, {**self.data, **(data or {})}, kind=self.kind)
        else:
            log_event(self.device, event_type, f"no push (alert was muted), {detail}")
        return True


def retry_delay(key):
    """Seconds until a failed push of [key] is retried: PUSH_RETRY_SECONDS by how many
    failed in a row, then NOTIFICATION_COOLDOWN. Counts this failure."""
    n = state.PUSH_FAILURES.get(key, 0)
    state.PUSH_FAILURES[key] = n + 1
    return PUSH_RETRY_SECONDS[n] if n < len(PUSH_RETRY_SECONDS) else NOTIFICATION_COOLDOWN


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


def alert_muted(kind, ignore_quiet=False):
    """Why an alert of [kind] gets no push ("off", "quiet hours"), or None. [ignore_quiet]:
    the switch only."""
    if kind is None:
        return None
    if get_state(f"notify:{kind}", "1") != "1":
        return "off"
    if not ignore_quiet and in_quiet_hours():
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
