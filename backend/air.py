"""Air-out helper (#245): outdoor weather from Open-Meteo, window openings detected from
the main node's CO₂ and temperature, and CO₂ excursions while someone is home. Logged as
WINDOW_OPEN / WINDOW_CLOSED / AIR_HIGH / AIR_OK (observe-only 2026-10-19..24); since step 2
it also pushes, under the "air" switch (#82), as data event AIR (silent channel in the app):
"Time to air out" (device co2, again every AIR_REMINDER_HOURS while CO₂ stays high, replaced
by "Air is fresh again"), "Close the window?" and "Window still open?" when leaving (device
window). Quiet hours postpone the pushes instead of dropping them, like the watchdog.
"""

import json
import logging
import math
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from config import (
    AIR_CHECK_INTERVAL, AIR_CO2_HIGH, AIR_CO2_OK, AIR_DEVICE, AIR_REMINDER_HOURS, AIR_RETRY_MINUTES,
    QUIET_MINUTES_BEFORE_ALERT, WEATHER_INTERVAL, WEATHER_LAT, WEATHER_LON, WEATHER_MAX_AGE,
    WINDOW_CLOSE_AFTER_MINUTES, WINDOW_CLOSE_CO2_FRESH, WINDOW_CLOSE_CO2_RISE, WINDOW_CLOSE_COLDER_OUTSIDE,
    WINDOW_CLOSE_TEMP_RISE, WINDOW_CO2_DROP, WINDOW_MAX_MINUTES, WINDOW_MINUTES,
    WINDOW_TEMP_DROP, WINDOW_TEMP_FALLING,
)
from store import db, get_state, log_event, parse_ts, set_state, utcnow
from alerts import alert, alert_muted
from ingest import any_radar_quiet_minutes

log = logging.getLogger(__name__)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
CO2_DATA = {"event": "AIR", "device": "co2"}
WINDOW_DATA = {"event": "AIR", "device": "window"}
WEATHER = None   # last good reading: {"at": unix time, "temp", "humidity", "precipitation"}
_last_fetch = 0.0


# -------------------------
# WEATHER (Open-Meteo, free, no key)
# -------------------------
def fetch_weather():
    """Current outdoor values, or raises. Open-Meteo refreshes them every 15 minutes."""
    query = urllib.parse.urlencode({
        "latitude": WEATHER_LAT, "longitude": WEATHER_LON,
        "current": "temperature_2m,relative_humidity_2m,precipitation",
    })
    req = urllib.request.Request(f"{WEATHER_URL}?{query}", headers={"User-Agent": "home-ping"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        cur = json.load(resp)["current"]
    return {"at": time.time(), "temp": float(cur["temperature_2m"]),
            "humidity": float(cur["relative_humidity_2m"]), "precipitation": float(cur["precipitation"])}


def update_weather():
    """Fetch every WEATHER_INTERVAL; no coordinates in .env (dev, tests) = no weather."""
    global WEATHER, _last_fetch
    if not (WEATHER_LAT and WEATHER_LON) or time.time() - _last_fetch < WEATHER_INTERVAL:
        return
    _last_fetch = time.time()
    try:
        WEATHER = fetch_weather()
    except Exception as e:   # keep the last value; weather() drops it once it's too old
        log.warning("weather fetch failed: %s", e)


def weather():
    """The last weather, or None when there is none or it's older than WEATHER_MAX_AGE."""
    if WEATHER is None or time.time() - WEATHER["at"] > WEATHER_MAX_AGE:
        return None
    return WEATHER


def absolute_humidity(temp, rh):
    """g/m³ of water. Airing only dries the flat when outside holds less than inside,
    whatever the %RH: 5 °C at 90 % is ~6.1 g/m³, 22 °C at 50 % is ~9.7 g/m³."""
    return 6.112 * math.exp(17.67 * temp / (temp + 243.5)) * rh * 2.1674 / (273.15 + temp)


def outside_text(w):
    if w is None:
        return "no weather"
    rain = ", raining" if w["precipitation"] > 0 else ""
    return f"outside {w['temp']:.1f} °C {w['humidity']:.0f} %{rain}"


# -------------------------
# CHECKS
# -------------------------
def recent_readings(now):
    """The air node's last WINDOW_MINUTES of readings: [(ts, temp, humidity, co2 or None)]."""
    since = (now - timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    until = now.strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        rows = conn.execute("""
            SELECT ts, temp, humidity, co2, co2_ok FROM readings
            WHERE device = ? AND ts BETWEEN ? AND ? ORDER BY ts
        """, (AIR_DEVICE, since, until)).fetchall()
    return [(parse_ts(ts), t, h, c if ok == 1 else None) for ts, t, h, c, ok in rows]


def peak_time(series):
    """When the newest maximum of [(ts, value)] was read: where a fall started."""
    top = max(v for _, v in series)
    return [ts for ts, v in series if v == top][-1]


def check_window(now, temp_series, co2_series):
    """A window opening is a fast fall of CO₂ and/or temperature. When the temperature fell
    too, it is also what shows the window closing again: it climbs back within minutes,
    while CO₂ can already rise with the window still open (people next to the sensor)."""
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    temps = [v for _, v in temp_series]
    co2s = [v for _, v in co2_series]
    co2_drop = co2s[-1] - max(co2s) if co2s else 0
    temp_drop = temps[-1] - max(temps) if temps else 0
    spell = get_state("air:window", "")
    if spell:
        since, before = spell.split("|")
        minutes = (now - parse_ts(since)).total_seconds() / 60
        # CO₂ reacts first, so whether the temperature fell too is only known later: compare
        # with the temperature from before the opening
        temp_fell = bool(before and temps) and float(before) - min(temps) >= WINDOW_TEMP_FALLING
        why = None
        if minutes >= 2:   # not the same few readings that opened it
            if temp_fell and temps[-1] - min(temps) >= WINDOW_CLOSE_TEMP_RISE:
                why = "temperature rising"
            elif (not temp_fell and minutes >= WINDOW_MINUTES and co2s
                  and co2s[-1] - min(co2s) >= WINDOW_CLOSE_CO2_RISE):
                why = "CO₂ rising"
        if why is None and minutes >= WINDOW_MAX_MINUTES:
            why = "no close seen"
        if why:
            inside = ", ".join(filter(None, [temps and f"{temps[-1]:.1f} °C",
                                             co2s and f"CO₂ {co2s[-1]:.0f} ppm"]))
            log_event(AIR_DEVICE, "WINDOW_CLOSED", f"after {minutes:.0f} min ({why}), inside {inside}")
            set_state("air:window", "")
            set_state("air:window_closed", stamp)
        else:
            nudge_close(since, minutes, temps, co2s)
        return
    closed = get_state("air:window_closed", "")
    # The readings from before a short opening stay in the window for a while: don't
    # count the same fall twice
    if closed and now - parse_ts(closed) < timedelta(minutes=WINDOW_MINUTES):
        return
    if co2_drop <= -WINDOW_CO2_DROP or temp_drop <= -WINDOW_TEMP_DROP:
        # The spell starts where the fall that crossed the threshold started, not when it
        # crossed it (a few minutes later), so WINDOW_CLOSED's duration is the real one
        started = peak_time(temp_series if temp_drop <= -WINDOW_TEMP_DROP else co2_series)
        set_state("air:window", f"{started:%Y-%m-%d %H:%M:%S}|{max(temps) if temps else ''}")
        log_event(AIR_DEVICE, "WINDOW_OPEN",
                  f"CO₂ {co2_drop:+.0f} ppm, {temp_drop:+.1f} °C in {WINDOW_MINUTES} min; "
                  f"{outside_text(weather())}")


def nudge_close(since, minutes, temps, co2s):
    """"Close the window?" once per spell: open long enough, the air is fresh and outside is
    much colder, so leaving it open only cools the flat. Postponed in quiet hours; a failed
    send isn't retried (it's only a hint)."""
    w = weather()
    if (get_state("air:window_nudged", "") == since or minutes < WINDOW_CLOSE_AFTER_MINUTES
            or not (co2s and temps and w) or co2s[-1] >= WINDOW_CLOSE_CO2_FRESH
            or w["temp"] > temps[-1] - WINDOW_CLOSE_COLDER_OUTSIDE):
        return
    if alert_muted("air") == "quiet hours":
        return
    set_state("air:window_nudged", since)
    alert(AIR_DEVICE, "WINDOW_CLOSE", f"open {minutes:.0f} min, CO₂ {co2s[-1]:.0f} ppm, "
          f"inside {temps[-1]:.1f} °C, {outside_text(w)}", "Close the window?",
          f"Open for {minutes:.0f} min, CO₂ is down to {co2s[-1]:.0f} ppm and it's {w['temp']:.0f} °C "
          "outside: the air is fresh, more only cools the flat.", WINDOW_DATA, kind="air")


def window_open_on_leave():
    """"Window still open?" when the phone's geofence arms occupancy (#238) during a spell."""
    spell = get_state("air:window", "")
    if not spell:
        return
    minutes = (utcnow() - parse_ts(spell.split("|")[0])).total_seconds() / 60
    alert(AIR_DEVICE, "WINDOW_LEFT_OPEN", f"open {minutes:.0f} min", "Window still open?",
          f"You left home and a window has been open for {minutes:.0f} min.", WINDOW_DATA, kind="air")


def air_advice(temp, humidity):
    """What the "Time to air out" push suggests. Rain or humid outside air don't hold it back
    (the CO₂ still has to go), they change the advice."""
    w = weather()
    if w is not None:
        if w["precipitation"] > 0:
            return "it's raining, tilt a window for a while"
        if (temp is not None and humidity is not None
                and absolute_humidity(w["temp"], w["humidity"]) >= absolute_humidity(temp, humidity)):
            return "the outside air is humid, keep it short"
    return "open a window for 5-10 minutes"


def push_co2(now, rows, co2s, high):
    """Push "Time to air out" when it's due: not pushed yet in this excursion (or the last push
    is AIR_REMINDER_HOURS old), CO₂ still above AIR_CO2_HIGH, someone home, no quiet hours.
    Returns why it didn't push, or None."""
    last = get_state("air:co2_push", "")
    if last and now - parse_ts(last) < timedelta(hours=AIR_REMINDER_HOURS):
        return "pushed"
    if not co2s or co2s[-1] < AIR_CO2_HIGH:
        return "below threshold"
    if any_radar_quiet_minutes() >= QUIET_MINUTES_BEFORE_ALERT:
        return "nobody home"
    if alert_muted("air") == "quiet hours":
        return "quiet hours"
    env = [(t, h) for _, t, h, _ in rows if t is not None and h is not None]
    temp, humidity = env[-1] if env else (None, None)
    hours = (now - parse_ts(high)).total_seconds() / 3600
    still = f"still {co2s[-1]:.0f} ppm after {hours:.0f} h" if hours >= 1 else f"{co2s[-1]:.0f} ppm"
    advice = air_advice(temp, humidity)
    result = alert(AIR_DEVICE, "AIR_HIGH", f"CO₂ {still}, {outside_text(weather())}: {advice}",
                   "Time to air out", f"CO₂ {still}: {advice}.", CO2_DATA, kind="air")
    stamp = now if result else now - timedelta(hours=AIR_REMINDER_HOURS, minutes=-AIR_RETRY_MINUTES)
    set_state("air:co2_push", stamp.strftime("%Y-%m-%d %H:%M:%S"))
    if result == "sent":
        set_state("air:co2_shown", "1")
    return None


def check_co2(now, rows, co2s):
    """CO₂ above AIR_CO2_HIGH for the whole window starts an excursion; it ends below
    AIR_CO2_OK. push_co2() runs every check while it lasts, so a push held back (nobody home,
    quiet hours) goes out once that's over. [rows] are recent_readings(); the node sends
    CO₂ and temperature/humidity in separate messages, so separate rows."""
    high = get_state("air:co2_high", "")
    if high:
        if co2s and co2s[-1] < AIR_CO2_OK:
            minutes = (now - parse_ts(high)).total_seconds() / 60
            detail = f"CO₂ {co2s[-1]:.0f} ppm, {minutes:.0f} min after AIR_HIGH"
            if get_state("air:co2_shown", "") == "1":   # replace the push on the phone
                alert(AIR_DEVICE, "AIR_OK", detail, "Air is fresh again",
                      f"CO₂ is down to {co2s[-1]:.0f} ppm.", CO2_DATA, kind="air")
            else:
                log_event(AIR_DEVICE, "AIR_OK", detail)
            for key in ("air:co2_high", "air:co2_push", "air:co2_shown"):
                set_state(key, "")
        else:
            push_co2(now, rows, co2s, high)
        return
    covered = rows[0][0] <= now - timedelta(minutes=WINDOW_MINUTES - 1)
    if not (covered and len(co2s) >= 5 and min(co2s) >= AIR_CO2_HIGH):
        return
    high = now.strftime("%Y-%m-%d %H:%M:%S")
    set_state("air:co2_high", high)
    why = push_co2(now, rows, co2s, high)
    if why:
        log_event(AIR_DEVICE, "AIR_HIGH",
                  f"no push yet ({why}), CO₂ {co2s[-1]:.0f} ppm for {WINDOW_MINUTES} min, {outside_text(weather())}")


def check_air(now=None):
    now = now or utcnow()
    rows = recent_readings(now)
    # Nothing recent (node down, sensor warming up): no verdict either way
    if not rows or now - rows[-1][0] > timedelta(minutes=3):
        return
    temp_series = [(ts, t) for ts, t, _, _ in rows if t is not None]
    co2_series = [(ts, c) for ts, _, _, c in rows if c is not None]
    check_window(now, temp_series, co2_series)
    check_co2(now, rows, [c for _, c in co2_series])


def air_loop():
    while True:
        time.sleep(AIR_CHECK_INTERVAL)
        update_weather()
        try:
            check_air()
        except Exception as e:
            log.warning("air check failed: %s", e)
