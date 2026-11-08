"""#351: a fridge too warm comes through quiet hours, and so does its "back to normal"; too cold
and warming up still wait for the end of quiet hours; the "fridge" switch mutes them all."""
import time
from datetime import datetime, timedelta

from support import check


def test_issue351_fridge_warm_quiet(env):
    import server
    import state, alerts, fridge
    from store import db, get_state, set_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushed = []
    m.send.side_effect = lambda msg: pushed.append(msg["data"]["alert"] or "normal") or "id"

    def reading(alert, temp):
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, alert, uptime) VALUES "
                         "('fridge_temp_esp32', datetime('now'), ?, ?, 90000)", (temp, alert))

    def later():
        state.LAST_NOTIFICATION.clear()   # each step past its cooldown
        fridge.check_fridge_alerts(time.time())

    def quiet(on):
        now = datetime.now()
        set_state("quiet_hours", "1" if on else "0")
        set_state("quiet_start", (now - timedelta(hours=1)).strftime("%H:%M"))
        set_state("quiet_end", (now + timedelta(hours=1)).strftime("%H:%M"))

    quiet(True)
    check("quiet hours are on now", alerts.in_quiet_hours())

    # --- warming up: still waits for the end of quiet hours
    reading("warm_low", 6.0); later()
    check("warm_low in quiet hours: muted", pushed == [] and get_state("fridge_alert_shown") == "muted")

    # --- getting worse: too warm comes through
    reading("warm_critical", 9.0); later()
    check("warm_critical in quiet hours: pushed", pushed == ["warm_critical"]
          and get_state("fridge_alert_shown") == "sent" and get_state("fridge_alert_urgent") == "1")

    # --- cooling down via warm_low, then normal: the recovery replaces it, still in quiet hours
    reading("warm_low", 7.0); later()
    check("critical -> warm_low: no push, still urgent", pushed == ["warm_critical"]
          and get_state("fridge_alert_urgent") == "1")
    reading("", 3.5); later()
    check("back to normal comes through quiet hours", pushed == ["warm_critical", "normal"]
          and get_state("fridge_alert_urgent") == "")

    # --- sustained warmth too
    pushed.clear()
    reading("warm_critical_duration", 6.0); later()
    check("warm_critical_duration in quiet hours: pushed", pushed == ["warm_critical_duration"])
    reading("", 3.5); later()
    check("...and its recovery", pushed == ["warm_critical_duration", "normal"])

    # --- too cold: waits; its end isn't urgent either
    pushed.clear()
    reading("cold", -1.0); later()
    check("cold in quiet hours: muted", pushed == [] and get_state("fridge_alert_shown") == "muted")
    reading("", 1.0); later()
    check("cold over in quiet hours: nothing", pushed == [])

    # --- a sent warm_low ending in quiet hours: muted like before (not urgent)
    quiet(False)
    reading("warm_low", 6.0); later()
    check("warm_low outside quiet hours: pushed", pushed == ["warm_low"])
    quiet(True)
    reading("", 3.5); later()
    check("its recovery in quiet hours: muted", pushed == ["warm_low"] and get_state("fridge_alert_state") == "")

    # --- the switch still mutes too warm
    pushed.clear()
    set_state("notify:fridge", "0")
    reading("warm_critical", 9.0); later()
    check("switch off: warm_critical muted", pushed == [] and get_state("fridge_alert_shown") == "muted")
    set_state("notify:fridge", "1")
    later()
    check("switch on again, still in quiet hours: pushed now", pushed == ["warm_critical"]
          and get_state("fridge_alert_urgent") == "1")
