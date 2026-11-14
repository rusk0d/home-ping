"""Occupancy queries over the radars' raw presence_readings (#425, from store.py): the 24 h
stats, per-radar and combined confidence and the combined "someone home now"; and the flat's
occupied / vacant changes logged from it (#422). contracts/db-schema.md ("Occupancy logic") and api.md.
"""

import time

import state
from config import CONFIDENCE_MINUTES, FLAT_VACANT_MINUTES
from store import db, get_state, log_event, parse_ts, set_state


def occupancy_stats(hours=24):
    """Occupancy over a window, using each radar's real sample spacing."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT device,
                   COUNT(*),
                   SUM(occupied),
                   MIN(ts),
                   MAX(ts)
            FROM presence_readings
            WHERE ts >= datetime('now', ?)
            GROUP BY device
        """, (f"-{hours} hours",))
        rows = c.fetchall()

    if not rows:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}

    # The radar with the highest occupancy % answers, all three values from it (#227):
    # taking the coverage from another radar mixed sensors once their spans differed.
    # Ties (e.g. nobody home: 0 % on both) go to the longer span.
    best = None   # (pct, span_h, occupied fraction)
    for device, total, occupied, first_ts, last_ts in rows:
        if not total:
            continue
        frac = occupied / total

        # Real span this device actually covered
        t0 = parse_ts(first_ts)
        t1 = parse_ts(last_ts)
        span_h = max((t1 - t0).total_seconds() / 3600, 0)

        if best is None or (frac * 100, span_h) > best[:2]:
            best = (frac * 100, span_h, frac)

    if best is None:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}
    pct, span_h, frac = best
    return {
        "avg_occupancy": round(pct, 0),
        "occupied_hours": round(span_h * frac, 2),
        "coverage_hours": round(span_h, 1),
    }


def calculate_confidence(device, minutes=CONFIDENCE_MINUTES):
    """Occupancy confidence for one radar over a time window (0-100), None without data."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ? AND ts >= datetime('now', ?)
        """, (device, f"-{minutes} minutes"))
        rows = c.fetchall()

    if not rows:
        return None

    return int((sum(r[0] for r in rows) / len(rows)) * 100)


def get_combined_occupancy():
    """1 if any radar has 2+ occupied rows in its last 3 reports (last 2 min), 0 if not,
    None if no radar reported in the last 2 minutes."""
    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT device FROM presence_readings")
        devices = [r[0] for r in c.fetchall()]

        result = None
        for d in devices:
            c.execute("""
                SELECT occupied FROM presence_readings
                WHERE device = ? AND ts >= datetime('now','-2 minutes')
                ORDER BY id DESC LIMIT 3
            """, (d,))
            rows = [r[0] for r in c.fetchall()]
            if rows and result is None:
                result = 0
            if sum(rows) >= 2:
                result = 1
    return result


def calculate_combined_confidence(minutes=CONFIDENCE_MINUTES):
    """Per-radar confidence over the window, then the highest of them; None without data."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT device, AVG(occupied)
            FROM presence_readings
            WHERE ts >= datetime('now', ?)
            GROUP BY device
        """, (f"-{minutes} minutes",))
        rows = c.fetchall()

    if not rows:
        return None
    return int(max(r[1] for r in rows) * 100)


def note_flat_occupancy(now=None):
    """Log FLAT_OCCUPANCY when the flat becomes occupied or vacant (#422), on each presence
    message. Occupied as soon as get_combined_occupancy() is 1; vacant once it has been 0 for
    FLAT_VACANT_MINUTES. The radars' raw flag flips every 10-20 s and was logged on every flip
    (OCCUPANCY_CHANGE, ~2,000 rows a day); this is a few a day. system_state flat_occupied
    ('1' / '0', missing until the first change) survives a restart; after one, the vacant hold
    starts over. No radar in the last 2 min (None): nothing changes."""
    now = time.time() if now is None else now
    occupied = get_combined_occupancy()
    if occupied is None:
        return None
    shown = get_state("flat_occupied", "")
    if occupied:
        state.FLAT["last_occupied"] = now
        if shown != "1":
            set_state("flat_occupied", "1")
            log_event("system", "FLAT_OCCUPANCY", "occupied")
            return "occupied"
        return None
    if shown == "0":
        return None
    last = state.FLAT.setdefault("last_occupied", now)
    if now - last >= FLAT_VACANT_MINUTES * 60:
        set_state("flat_occupied", "0")
        log_event("system", "FLAT_OCCUPANCY", f"vacant (no one for {FLAT_VACANT_MINUTES} min)")
        return "vacant"
    return None
