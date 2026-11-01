"""Baseline deviation alerts: detect unusual readings for time of day (#229)."""

import logging
from datetime import datetime, timedelta

import alerts
from config import NOTIFICATION_COOLDOWN
from store import db, get_state, set_state, parse_ts, utcnow

log = logging.getLogger(__name__)

# Per-metric thresholds for minimum absolute deviation to prevent alerts when variance is tiny
MIN_ABS_DEV = {"temp": 1.5, "humidity": 5.0, "co2": 100, "pressure": 2.0}  # °C, %, ppm, hPa
Z_THRESHOLD = 3.0  # Alert when |z| > this
CONSECUTIVE_HOURS = 2  # Alert after this many consecutive anomalous hours


def median(values):
    """Median of a list."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    return sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def mad(values, center=None):
    """Median Absolute Deviation."""
    if not values or center is None:
        return None
    abs_devs = [abs(v - center) for v in values]
    return median(abs_devs)


def compute_robust_zscore(value, history, metric_name):
    """Compute z-score using median and MAD, robust to outliers.

    Args:
        value: Current reading
        history: List of historical values for the same hour-of-day
        metric_name: 'temp', 'humidity', 'co2', 'pressure' — for min_abs_dev lookup

    Returns:
        (z_score, median, mad) or (None, None, None) if insufficient data
    """
    if not history or len(history) < 8:  # Need ~2 weeks of data
        return None, None, None

    med = median(history)
    deviation = mad(history, med)

    if med is None or deviation is None:
        return None, None, None

    # Use minimum absolute deviation to prevent alerts when variance is tiny
    min_dev = MIN_ABS_DEV.get(metric_name, 1.0)
    denominator = max(deviation, min_dev)

    z = (value - med) / denominator if denominator > 0 else 0
    return z, med, deviation


def get_hour_of_day(ts_str):
    """Extract day-of-week and hour from timestamp ('YYYY-MM-DD HH:00:00')."""
    try:
        dt = parse_ts(ts_str)
        day_name = dt.strftime("%A")  # Monday, Tuesday, ...
        hour = dt.strftime("%H")
        return f"{day_name} {hour}:00"
    except:
        return None


def get_historical_values(metric_col, hour_of_day, lookback_days=28):
    """Query hourly table for same hour-of-day from the past N days."""
    hour_key = f"{hour_of_day}"

    with db() as conn:
        cutoff = (utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

        # SQLite strftime for day and hour of the timestamp
        query = f"""
            SELECT {metric_col} FROM hourly
            WHERE {metric_col} IS NOT NULL
            AND strftime('%A %H:00', hour) = ?
            AND hour >= ?
            ORDER BY hour
        """

        rows = conn.execute(query, (hour_key, cutoff)).fetchall()
        return [row[0] for row in rows]


def check_baseline_deviations():
    """Check the most recent complete hour in the hourly table for deviations.

    Returns: list of (metric, z_score, median, current_value, detail) tuples for alerts
    """
    with db() as conn:
        # Get the most recent hour (the one just completed)
        row = conn.execute(
            "SELECT hour, temp_avg, humidity_avg, co2_avg, pressure_avg FROM hourly ORDER BY hour DESC LIMIT 1"
        ).fetchone()

        if not row:
            return []

        hour_ts, temp, humidity, co2, pressure = row
        hour_of_day = get_hour_of_day(hour_ts)

        if not hour_of_day:
            return []

        alerts_to_send = []

        # Check each metric
        for metric_name, value in [("temp", temp), ("humidity", humidity), ("co2", co2), ("pressure", pressure)]:
            if value is None:
                continue

            history = get_historical_values(f"{metric_name}_avg", hour_of_day, lookback_days=28)
            z, med, dev = compute_robust_zscore(value, history, metric_name)

            if z is None or abs(z) <= Z_THRESHOLD:
                continue  # Not anomalous

            # This metric is anomalous. Check excursion state.
            state_key = f"baseline_excursion:{metric_name}"
            count_key = f"baseline_excursion_count:{metric_name}"

            excursion_start = get_state(state_key, None)
            excursion_count = int(get_state(count_key, "0"))

            if excursion_start is None:
                # First anomalous hour: initialize
                set_state(state_key, hour_ts)
                set_state(count_key, "1")
                log.info(f"Baseline: {metric_name} anomaly started at {hour_ts}, z={z:.1f}")
            else:
                # Ongoing anomaly
                excursion_count += 1
                set_state(count_key, str(excursion_count))

                if excursion_count >= CONSECUTIVE_HOURS:
                    # Alert on 2nd+ consecutive hour
                    unit = {"temp": "°C", "humidity": "%", "co2": "ppm", "pressure": "hPa"}[metric_name]
                    detail = f"{metric_name} z={z:.1f} (unusual for {hour_of_day}), {metric_name}={value:.1f} {unit}, typical {med:.1f} {unit}"
                    alerts_to_send.append((metric_name, z, med, value, detail))
                    log.info(f"Baseline alert: {detail}")

        # Check for end of excursions (value back to normal)
        for metric_name in ["temp", "humidity", "co2", "pressure"]:
            state_key = f"baseline_excursion:{metric_name}"
            count_key = f"baseline_excursion_count:{metric_name}"

            excursion_start = get_state(state_key, None)
            if excursion_start is None:
                continue  # No excursion for this metric

            value = {"temp": temp, "humidity": humidity, "co2": co2, "pressure": pressure}[metric_name]
            if value is None:
                continue

            history = get_historical_values(f"{metric_name}_avg", hour_of_day, lookback_days=28)
            z, med, dev = compute_robust_zscore(value, history, metric_name)

            if z is not None and abs(z) <= Z_THRESHOLD:
                # Back to normal
                set_state(state_key, None)
                set_state(count_key, "0")
                log.info(f"Baseline: {metric_name} excursion ended, back to normal")

                # Send "back to normal" notification
                unit = {"temp": "°C", "humidity": "%", "co2": "ppm", "pressure": "hPa"}[metric_name]
                detail = f"{metric_name} back to normal for {hour_of_day}, reading {value:.1f} {unit}"
                alerts_to_send.append((metric_name, 0, med, value, f"sent, {detail}"))

        return alerts_to_send


def send_baseline_alerts():
    """Check for deviations and send alerts."""
    deviations = check_baseline_deviations()

    for metric_name, z, med, value, detail in deviations:
        cooldown_key = f"baseline:{metric_name}"

        # Use existing alert plumbing
        alerts.alert(
            device="system",
            event_type="BASELINE_DEVIATION",
            detail=detail,
            title="Unusual reading",
            body=f"{metric_name.capitalize()} is unusual for this time of day",
            data={"metric": metric_name, "z_score": str(z)},
            cooldown_key=cooldown_key,
            kind="baseline"
        )
