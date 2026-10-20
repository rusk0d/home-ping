#!/usr/bin/env python3
"""Validation: verify API calculations against reimplemented versions from docstrings.

This script reimplements key API functions based on their contracts (docstrings),
runs both versions against production data, and reports any discrepancies.

A test suite and implementation can share assumptions—this catches bugs where
both are wrong in the same way by computing answers a different route.
"""

import sys
from datetime import datetime, timedelta, timezone

from _common import API_BASE, api_get, connect_ro
from config import CONFIDENCE_MINUTES


def utcnow():
    """Naive UTC now (comparable with stored timestamps)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_ts(ts_str):
    """Parse stored timestamp 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")


def load_db():
    """Load raw tables from production database."""
    conn = connect_ro()

    presence = [dict(row) for row in conn.execute("SELECT * FROM presence_readings ORDER BY ts")]
    readings = [dict(row) for row in conn.execute("SELECT * FROM readings ORDER BY ts")]
    hourly = [dict(row) for row in conn.execute("SELECT * FROM hourly ORDER BY hour")]

    conn.close()
    return presence, readings, hourly


# ---- Reimplemented functions from contracts ----

def occupancy_stats(presence, hours=24):
    """From store.py docstring: 'Occupancy over a window, using each radar's real sample spacing.'

    Returns: {avg_occupancy: %, occupied_hours: hours, coverage_hours: span}
    """
    now = utcnow()
    cutoff = now - timedelta(hours=hours)
    filtered = [p for p in presence if parse_ts(p["ts"]) >= cutoff]

    if not filtered:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}

    # Group by device
    by_device = {}
    for p in filtered:
        device = p["device"]
        by_device.setdefault(device, []).append(p)

    best = None
    for device, rows in by_device.items():
        if not rows:
            continue

        occupied = sum(1 for r in rows if r["occupied"])
        total = len(rows)
        frac = occupied / total
        pct = frac * 100

        # Real span of coverage
        ts_values = [parse_ts(r["ts"]) for r in rows]
        span_h = max((max(ts_values) - min(ts_values)).total_seconds() / 3600, 0)

        # Highest occupancy %, ties broken by longer span
        if best is None or (pct, span_h) > (best[0], best[1]):
            best = (pct, span_h, frac)

    if best is None:
        return {"avg_occupancy": None, "occupied_hours": None, "coverage_hours": None}

    pct, span_h, frac = best
    return {
        "avg_occupancy": round(pct, 0),
        "occupied_hours": round(span_h * frac, 2),
        "coverage_hours": round(span_h, 1),
    }


def calculate_combined_confidence(presence, minutes=CONFIDENCE_MINUTES):
    """From store.py: 'Per-radar confidence over the window, then the highest of them;
    None without data.'

    Confidence = % of rows occupied. Returns 0-100 or None.
    """
    now = utcnow()
    cutoff = now - timedelta(minutes=minutes)
    filtered = [p for p in presence if parse_ts(p["ts"]) >= cutoff]

    if not filtered:
        return None

    # Per-device average
    by_device = {}
    for p in filtered:
        device = p["device"]
        by_device.setdefault(device, []).append(p["occupied"])

    confidences = [sum(vals) / len(vals) * 100 for vals in by_device.values() if vals]
    return int(max(confidences)) if confidences else None


def get_combined_occupancy(presence):
    """From store.py: '1 if any radar has 2+ occupied rows in its last 3 reports
    (last 2 min), 0 if not, None if no radar reported in the last 2 minutes.'
    """
    now = utcnow()
    cutoff = now - timedelta(minutes=2)
    filtered = [p for p in presence if parse_ts(p["ts"]) >= cutoff]

    if not filtered:
        return None

    # Last 3 reports per device
    by_device = {}
    for p in filtered:
        device = p["device"]
        by_device.setdefault(device, []).append(p)

    result = None
    for device, rows in by_device.items():
        last_3 = sorted(rows, key=lambda r: r["ts"], reverse=True)[:3]
        occupied_vals = [r["occupied"] for r in last_3]

        if occupied_vals:
            if result is None:
                result = 0
            if sum(occupied_vals) >= 2:
                result = 1

    return result


def pressure_3h_change(readings):
    """From api.py /api/air: 'Pressure as it was 3 hours ago' compared to latest.

    Returns pressure_now - pressure_3h_ago, rounded to 0.1, or None if no data.
    """
    now = utcnow()

    # Latest pressure
    recent = sorted(
        [r for r in readings if r["pressure"] is not None],
        key=lambda r: r["ts"],
        reverse=True
    )
    if not recent:
        return None

    current = recent[0]["pressure"]

    # Pressure 3 hours ago
    cutoff = now - timedelta(hours=3)
    old = sorted(
        [r for r in readings if r["pressure"] is not None and parse_ts(r["ts"]) <= cutoff],
        key=lambda r: r["ts"],
        reverse=True
    )
    if not old:
        return None

    past = old[0]["pressure"]
    return round(current - past, 1)


# ---- Validation and reporting ----

class Validator:
    def __init__(self, presence, readings, hourly):
        self.presence = presence
        self.readings = readings
        self.hourly = hourly
        self.failures = []
        self.passed = 0

    def check(self, name, reimp, api_data, precision=None):
        """Compare reimplemented vs API value."""
        if reimp is None and api_data is None:
            print(f"  ✓ {name}: both None")
            self.passed += 1
            return True

        if reimp is None or api_data is None:
            msg = f"{name}: reimp={reimp}, api={api_data}"
            print(f"  ✗ {msg}")
            self.failures.append(msg)
            return False

        # Compare with rounding
        r = round(reimp, precision) if precision is not None and isinstance(reimp, float) else reimp
        a = round(api_data, precision) if precision is not None and isinstance(api_data, float) else api_data

        if r == a:
            print(f"  ✓ {name}: {r}")
            self.passed += 1
            return True
        else:
            msg = f"{name}: reimp={r}, api={a}"
            print(f"  ✗ {msg}")
            self.failures.append(msg)
            return False

    def validate_all(self):
        """Run all validations."""
        print("📊 occupancy_stats(24)")
        reimp = occupancy_stats(self.presence, 24)
        api = api_get("stats24h_combined")

        if api:
            for key in ["avg_occupancy", "occupied_hours", "coverage_hours"]:
                self.check(key, reimp.get(key), api.get(key), precision=1)

        print("\n📊 calculate_combined_confidence(15)")
        reimp = calculate_combined_confidence(self.presence, CONFIDENCE_MINUTES)
        api = api_get("latest")
        self.check("confidence", reimp, api.get("confidence") if api else None, precision=0)

        print("\n📊 get_combined_occupancy()")
        reimp = get_combined_occupancy(self.presence)
        self.check("occupied", reimp, api.get("occupied") if api else None)

        print("\n📊 pressure_3h_change")
        reimp = pressure_3h_change(self.readings)
        api_air = api_get("air")
        self.check("pressure_3h_change", reimp, api_air.get("pressure_3h_change") if api_air else None, precision=1)

        print("\n📊 hourly structure")
        api_hourly = api_get("hourly?days=1")
        if api_hourly:
            print(f"  ✓ API hourly: {len(api_hourly)} hours")
            self.passed += 1
        else:
            print("  ✗ Could not fetch hourly data")
            self.failures.append("hourly: API fetch failed")

    def report(self):
        """Print summary."""
        print("\n" + "="*60)
        if not self.failures:
            print(f"✅ All {self.passed} checks PASSED\n")
            return True
        else:
            print(f"⚠️  {len(self.failures)} failures, {self.passed} passed\n")
            for msg in self.failures:
                print(f"  • {msg}")
            print()
            return False


def main():
    print("🔍 IoT API Validation\n")

    # Check API
    print("🌐 Checking API...")
    if not api_get("devices"):
        print(f"❌ API unreachable at {API_BASE}")
        print("   Start with: python server.py")
        return False
    print("  ✓ API reachable\n")

    # Load data
    print("📂 Loading production database...")
    try:
        presence, readings, hourly = load_db()
        print(f"  ✓ {len(presence)} presence rows, {len(readings)} readings, {len(hourly)} hourly\n")
    except Exception as e:
        print(f"❌ Failed to load data: {e}")
        return False

    if not presence or not readings:
        print("❌ Insufficient data")
        return False

    # Validate
    print("="*60 + "\n")
    v = Validator(presence, readings, hourly)
    v.validate_all()
    return v.report()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
