"""Weekly summary push (#427): on Sunday evening, one push with the week instead of looking at
three screens: night CO₂ against the week before, times aired, fridge door pushes, node restarts
and server warnings. Its own switch "weekly" (#82); the app shows it on a silent channel.

In the summaries loop (every 5 min): from WEEKLY_HOUR on Sunday (local), once per ISO week
(system_state weekly_sent). Quiet hours postpone it to the first check after them; a failed send
is tried again at the next check. A Sunday the server missed is skipped, not sent on Monday.
"""

import logging
from datetime import datetime

from config import WEEKLY_DAY, WEEKLY_HOUR, WEEKLY_NIGHT_HOURS, device_info
from store import db, get_state, set_state
from alerts import alert, alert_muted

log = logging.getLogger(__name__)

DEVICE = "system"


def summary():
    """The last 7 days (and the night CO₂ of the 7 before), from hourly and events."""
    start, end = WEEKLY_NIGHT_HOURS
    with db() as conn:
        def night_co2(since, until):
            row = conn.execute("""
                SELECT AVG(co2_avg) FROM hourly
                WHERE co2_avg IS NOT NULL AND hour >= datetime('now', ?) AND hour < datetime('now', ?)
                  AND (CAST(strftime('%H', hour, 'localtime') AS INTEGER) >= ?
                       OR CAST(strftime('%H', hour, 'localtime') AS INTEGER) < ?)
            """, (since, until, start, end)).fetchone()
            return None if row[0] is None else round(row[0])

        def count(where, *args):
            return conn.execute(f"SELECT COUNT(*) FROM events WHERE ts >= datetime('now', '-7 days') AND {where}",
                                args).fetchone()[0]

        restarts = dict(conn.execute("""
            SELECT device, COUNT(*) FROM events
            WHERE type = 'RESTART' AND ts >= datetime('now', '-7 days')
            GROUP BY device ORDER BY 2 DESC, 1
        """).fetchall())
        # The server watchdogs that fired (WATCHDOG_BACKUP, ...), not their "OK again"
        warnings = [t[len("WATCHDOG_"):].lower() for (t,) in conn.execute("""
            SELECT DISTINCT type FROM events
            WHERE type LIKE 'WATCHDOG\\_%' ESCAPE '\\' AND type NOT LIKE '%\\_OK' ESCAPE '\\'
              AND ts >= datetime('now', '-7 days')
            ORDER BY 1
        """)]
        return {
            "night_co2": night_co2("-7 days", "+0 days"),
            "night_co2_before": night_co2("-14 days", "-7 days"),
            "aired": count("type = 'WINDOW_OPEN'"),
            "fridge_door": count("type = 'FRIDGE_DOOR_OPEN'"),
            "restarts": restarts,
            "warnings": warnings,
        }


def text(s):
    """The push body: "Night CO₂ 870 ppm (−60) · aired 21× · fridge door 3× · no restarts · server all fine"."""
    parts = []
    if s["night_co2"] is not None:
        co2 = f"Night CO₂ {s['night_co2']} ppm"
        if s["night_co2_before"] is not None:
            diff = s["night_co2"] - s["night_co2_before"]
            co2 += " (same as last week)" if diff == 0 else f" ({diff:+d} on last week)".replace("-", "−")
        parts.append(co2)
    parts.append(f"aired {s['aired']}×" if s["aired"] else "not aired")
    parts.append(f"fridge door {s['fridge_door']}×" if s["fridge_door"] else "fridge door fine")
    total = sum(s["restarts"].values())
    if total:
        nodes = ", ".join(f"{device_info(d)['name']} {n}" for d, n in s["restarts"].items())
        parts.append(f"{total} restart{'s' if total > 1 else ''} ({nodes})")
    else:
        parts.append("no restarts")
    if s["warnings"]:
        parts.append(f"server warning{'s' if len(s['warnings']) > 1 else ''}: " + ", ".join(s["warnings"]))
    else:
        parts.append("server all fine")
    body = " · ".join(parts)
    return body[0].upper() + body[1:]


def check_weekly(now=None):
    """Push the summary if it's Sunday evening and this week's hasn't gone out. Returns how the
    push went ("sent" / "muted" / False) or None when there was nothing to do."""
    now = now or datetime.now()
    if now.weekday() != WEEKLY_DAY or now.hour < WEEKLY_HOUR:
        return None
    week = now.strftime("%G-W%V")
    if get_state("weekly_sent", "") == week:
        return None
    if alert_muted("weekly") == "quiet hours":
        return None   # postponed: tried again at the next check
    body = text(summary())
    result = alert(DEVICE, "WEEKLY_SUMMARY", body, "Your week at home", body,
                   {"event": "WEEKLY", "device": DEVICE}, kind="weekly")
    if result:
        set_state("weekly_sent", week)
    return result
