"""#300: a muted fridge alert is pushed once quiet hours are over and gets no "back to
normal" of its own; a rebooted node's empty state isn't a recovery; cooling down between
two alerts doesn't push."""
import time

from support import check


def test_issue300_fridge_muted_reboot(env):
    import server
    import state, alerts, fridge
    from store import db, get_state, set_state
    server.prepare()

    m = alerts.messaging
    m.Message = lambda **kw: kw
    pushed = []
    m.send.side_effect = lambda msg: pushed.append(msg["data"]["alert"] or "normal") or "id"

    def reading(alert, temp=6.0, uptime=None):
        with db() as conn:
            conn.execute("INSERT INTO readings (device, ts, temp, alert, uptime) VALUES "
                         "('fridge_temp_esp32', datetime('now'), ?, ?, ?)", (temp, alert, uptime))

    def later():
        state.LAST_NOTIFICATION.clear()   # each step past its cooldown
        fridge.check_fridge_alerts(time.time())

    # --- muted (switch off, like quiet hours): saved, not pushed
    set_state("notify:fridge", "0")
    reading("warm_low"); later()
    check("muted: nothing pushed", pushed == [] and get_state("fridge_alert_shown") == "muted")
    later()
    check("still muted: still nothing", pushed == [])
    set_state("notify:fridge", "1")
    later()
    check("unmuted while still warm: pushed now", pushed == ["warm_low"] and get_state("fridge_alert_shown") == "sent")
    later()
    check("and only once", pushed == ["warm_low"])

    # --- a muted alert that ended before it was shown: no "back to normal"
    reading("", temp=3.0); later()
    pushed.clear()
    set_state("notify:fridge", "0")
    reading("warm_low"); later()
    reading("", temp=3.0); later()   # over while still muted
    set_state("notify:fridge", "1")
    later()
    check("muted alert ended: no push at all", pushed == [] and get_state("fridge_alert_state") == ""
          and get_state("fridge_alert_shown") == "")

    # --- reboot while warm: "" from a young node with a warm reading is held
    reading("warm_critical", temp=9.0, uptime=50000); later()
    check("critical pushed", pushed == ["warm_critical"])
    reading("", temp=9.0, uptime=60); later()
    check("rebooted node still warm: no false all-clear", pushed == ["warm_critical"]
          and get_state("fridge_alert_state") == "warm_critical")
    reading("warm_low", temp=8.2, uptime=1900); later()
    check("its timers coming back: still held", pushed == ["warm_critical"])
    reading("", temp=3.5, uptime=2500); later()
    check("young node but really cold again: recovery", pushed == ["warm_critical", "normal"])

    # --- cooling down between alerts on a node that has been up long: no push
    reading("warm_critical", temp=9.0, uptime=90000); later()
    reading("warm_low", temp=7.0, uptime=90060); later()
    check("critical -> warm_low: no 'warming up' push", pushed[-1] == "warm_critical"
          and get_state("fridge_alert_state") == "warm_low")
    reading("", temp=4.0, uptime=90120); later()
    check("then normal: recovery", pushed[-1] == "normal")

    # --- older firmware without uptime: a step down is taken as it comes
    reading("cold", temp=-1.0); later()
    reading("", temp=1.0); later()
    check("no uptime: recovery", pushed[-2:] == ["cold", "normal"])
