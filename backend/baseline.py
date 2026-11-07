"""Unusual readings for the time of day (#229, reworked in #297).

Each newly completed hour in `hourly` is checked once: its temperature, humidity and CO₂
averages against the same **local** hour of the day over the last BASELINE_DAYS days (the
household's rhythm follows the clock on the wall, so across a DST change the UTC hour shifts,
#302), as a robust z-score (median and MAD). BASELINE_HOURS unusual hours in a row (really
consecutive: a skipped hour starts over, #302) push "Unusual reading";
once the metric is back under BASELINE_Z_CLEAR, "Back to normal". Pressure isn't checked:
it has no daily rhythm, and fronts already push "Pressure falling fast" (#237).

system_state per metric: baseline_count:<metric> (unusual hours in a row),
baseline_seen:<metric> (the hour it was last checked) and baseline_alerted:<metric> ("sent" / "muted" while an excursion is on; saved only after
the push went out, so a failed one is retried on the next hour, no cooldown needed; a muted excursion ends
without a "Back to normal"). baseline_last_hour is the
newest hour checked.
"""

import logging
from datetime import timedelta

import alerts
from store import db, get_state, parse_ts, set_state, utcnow

log = logging.getLogger(__name__)

BASELINE_DAYS = 28
BASELINE_MIN_VALUES = 14   # half the window: a new install stays quiet for its first two weeks
BASELINE_Z = 3.0           # unusual above this |z|
BASELINE_Z_CLEAR = 2.0     # back to normal below this (hysteresis)
BASELINE_HOURS = 2         # unusual hours in a row before a push
BASELINE_MAX_AGE_HOURS = 2  # an older newest hour (node down, catch-up) isn't checked

# metric: (hourly column, unit, name in the push, floor for the spread)
METRICS = {
    "temp": ("temp_avg", "°C", "Temperature", 1.5),
    "humidity": ("humidity_avg", "%", "Humidity", 5.0),
    "co2": ("co2_avg", "ppm", "CO₂", 100.0),
}


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def robust_z(value, history, floor):
    """(z, median) of [value] against [history], or (None, None) with too little history.
    The spread is the MAD, at least [floor], so a very steady metric doesn't alert on noise."""
    if len(history) < BASELINE_MIN_VALUES:
        return None, None
    med = median(history)
    spread = max(median([abs(v - med) for v in history]), floor)
    return (value - med) / spread, med


def history(conn, col, hour):
    """[col] of the same local hour of the day over the BASELINE_DAYS days before [hour]."""
    since = (parse_ts(hour) - timedelta(days=BASELINE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(f"""
        SELECT {col} FROM hourly
        WHERE {col} IS NOT NULL AND hour >= ? AND hour < ?
          AND strftime('%H', hour, 'localtime') = strftime('%H', ?, 'localtime')
    """, (since, hour, hour)).fetchall()
    return [r[0] for r in rows]


def check_baseline():
    """Check the newest complete hour, once. Returns {metric: z} of what was checked."""
    with db() as conn:
        row = conn.execute("SELECT * FROM hourly ORDER BY hour DESC LIMIT 1").fetchone()
        if not row:
            return {}
        cols = [d[0] for d in conn.execute("SELECT * FROM hourly LIMIT 0").description]
        newest = dict(zip(cols, row))
        hour = newest["hour"]
        if hour == get_state("baseline_last_hour", None):
            return {}
        if utcnow() - parse_ts(hour) > timedelta(hours=BASELINE_MAX_AGE_HOURS + 1):
            set_state("baseline_last_hour", hour)
            return {}
        scores = {}
        for metric, (col, _, _, floor) in METRICS.items():
            if newest[col] is not None:
                z, med = robust_z(newest[col], history(conn, col, hour), floor)
                if z is not None:
                    scores[metric] = (z, med, newest[col])
        local = conn.execute("SELECT strftime('%H:00', ?, 'localtime')", (hour,)).fetchone()[0]
    set_state("baseline_last_hour", hour)

    prev_hour = (parse_ts(hour) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    for metric, (z, med, value) in scores.items():
        _, unit, name, _ = METRICS[metric]
        # Only a run of checked hours with no gap is "in a row"
        in_row = get_state(f"baseline_seen:{metric}", None) == prev_hour
        count = int(get_state(f"baseline_count:{metric}", "0")) if in_row else 0
        count = count + 1 if abs(z) > BASELINE_Z else 0
        set_state(f"baseline_count:{metric}", str(count))
        set_state(f"baseline_seen:{metric}", hour)
        alerted = get_state(f"baseline_alerted:{metric}", "") or ""
        detail = f"{metric}={value:.1f} {unit}, typical {med:.1f} {unit} at {local}, z={z:+.1f}"
        data = {"event": "BASELINE", "device": metric}
        if not alerted and count >= BASELINE_HOURS:
            direction = "high" if z > 0 else "low"
            result = alerts.alert("system", "BASELINE_DEVIATION", detail,
                                  title="Unusual reading",
                                  body=f"{name} is unusually {direction} for this time of day: "
                                       f"{value:.1f} {unit} (usually {med:.1f})",
                                  data=data, kind="baseline")
            if result:
                set_state(f"baseline_alerted:{metric}", result)
        elif alerted and abs(z) < BASELINE_Z_CLEAR:
            if alerted == "muted" or alerts.alert(
                    "system", "BASELINE_NORMAL", detail,
                    title="Back to normal", body=f"{name} is back to normal: {value:.1f} {unit}",
                    data=data, kind="baseline"):
                set_state(f"baseline_alerted:{metric}", "")
    return {m: s[0] for m, s in scores.items()}
