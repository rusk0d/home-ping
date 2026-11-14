"""Occupancy queries over the radars' raw presence_readings (#425, from store.py): the 24 h
stats, per-radar and combined confidence, the combined "someone home now" and a radar's previous
state. contracts/db-schema.md ("Occupancy logic") and api.md.
"""

from config import CONFIDENCE_MINUTES
from store import db, parse_ts


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


def get_last_occupancy(device):
    """Previous (second most recent) radar occupancy state."""
    with db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT occupied FROM presence_readings
            WHERE device = ?
            ORDER BY id DESC LIMIT 1 OFFSET 1
        """, (device,))
        row = c.fetchone()
    return row[0] if row else None
