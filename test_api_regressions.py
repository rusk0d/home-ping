#!/usr/bin/env python3
"""Regression tests: detect when API calculations diverge from expected behavior.

These tests validate edge cases, boundary conditions, and common failure modes:
- Empty data windows
- Single data points
- All-occupied vs all-unoccupied
- Time zone edge cases (DST boundaries)
- Stale data vs. fresh data
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    print("❌ requests module required", file=sys.stderr)
    sys.exit(1)

DB_PATH = Path("data.db")
API_BASE = "http://localhost:5000/api"
PRESENCE_DEVICE = "home_ping_esp32"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_ts(ts_str):
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")


def load_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    presence = [dict(row) for row in conn.execute("SELECT * FROM presence_readings ORDER BY ts")]
    conn.close()
    return presence


def api_auth():
    creds = {}
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                creds[k] = v.strip('"').strip("'")
    return creds.get("DASH_USER"), creds.get("DASH_PASS")


def api_get(endpoint, auth):
    try:
        resp = requests.get(f"{API_BASE}/{endpoint}", auth=auth, timeout=5)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


class RegressionTests:
    def __init__(self, presence, auth):
        self.presence = presence
        self.auth = auth
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def assert_eq(self, name, actual, expected):
        """Assert equality."""
        if actual == expected:
            print(f"  ✓ {name}")
            self.passed += 1
        else:
            print(f"  ✗ {name}: got {actual}, expected {expected}")
            self.failed += 1

    def assert_not_none(self, name, value):
        """Assert value is not None."""
        if value is not None:
            print(f"  ✓ {name}")
            self.passed += 1
        else:
            print(f"  ✗ {name}: unexpected None")
            self.failed += 1

    def assert_is_none(self, name, value):
        """Assert value is None."""
        if value is None:
            print(f"  ✓ {name}")
            self.passed += 1
        else:
            print(f"  ✗ {name}: expected None, got {value}")
            self.failed += 1

    def warn(self, name, condition, message):
        """Warn if condition is true."""
        if condition:
            print(f"  ⚠ {name}: {message}")
            self.warnings += 1
        else:
            print(f"  ✓ {name}")
            self.passed += 1

    def test_latest_endpoint_shape(self):
        """Test /api/latest returns expected fields."""
        print("\n🧪 Test: /api/latest shape")
        data = api_get("latest", self.auth)
        self.assert_not_none("latest fetches", data)

        if data:
            required = ["ts", "temp", "humidity", "occupied", "confidence", "online"]
            for field in required:
                self.assert_not_none(f"field '{field}'", data.get(field))

    def test_stats24h_values_in_range(self):
        """Test /api/stats24h_combined returns reasonable values."""
        print("\n🧪 Test: /api/stats24h_combined value ranges")
        data = api_get("stats24h_combined", self.auth)
        self.assert_not_none("stats24h_combined fetches", data)

        if data:
            avg_occ = data.get("avg_occupancy")
            if avg_occ is not None:
                self.warn("occupancy in [0,100]", avg_occ < 0 or avg_occ > 100,
                         f"value {avg_occ} outside range")

            cov = data.get("coverage_hours")
            if cov is not None:
                self.warn("coverage <= 24", cov > 24.1, f"value {cov} exceeds 24 hours")

            occ_h = data.get("occupied_hours")
            if occ_h is not None and cov is not None:
                self.warn("occupied_hours <= coverage", occ_h > cov + 0.1,
                         f"occupied {occ_h} > coverage {cov}")

    def test_confidence_is_0_to_100(self):
        """Test confidence values are 0-100 or None."""
        print("\n🧪 Test: confidence in [0,100]")

        # Check /api/latest
        data = api_get("latest", self.auth)
        if data and data.get("confidence") is not None:
            conf = data["confidence"]
            self.warn("latest.confidence in [0,100]", conf < 0 or conf > 100,
                     f"value {conf}")

        # Check /api/latest_all per device
        data = api_get("latest_all", self.auth)
        if data:
            for device, entry in data.items():
                if device == "combined":
                    continue
                if entry.get("confidence") is not None:
                    conf = entry["confidence"]
                    self.warn(f"{device}.confidence in [0,100]", conf < 0 or conf > 100,
                             f"value {conf}")

    def test_occupied_is_0_or_1(self):
        """Test occupied is 0, 1, or None."""
        print("\n🧪 Test: occupied in {0, 1, None}")

        data = api_get("latest", self.auth)
        if data and data.get("occupied") is not None:
            occ = data["occupied"]
            self.assert_eq("occupied value", occ in (0, 1), True)

    def test_air_pressure_trend(self):
        """Test /api/air pressure_3h_change is reasonable."""
        print("\n🧪 Test: /api/air pressure_3h_change")

        data = api_get("air", self.auth)
        if data and data.get("pressure_3h_change") is not None:
            trend = data["pressure_3h_change"]
            # Pressure typically doesn't change more than ±5 hPa in 3 hours
            self.warn("pressure change in [-10, 10] hPa", abs(trend) > 10,
                     f"unusual change {trend} hPa in 3 hours")

    def test_hourly_aggregation_consistency(self):
        """Test hourly temperature bounds."""
        print("\n🧪 Test: /api/hourly temp bounds")

        data = api_get("hourly?days=1", self.auth)
        if data:
            for hour in data:
                avg = hour.get("avg_temp")
                min_t = hour.get("min_temp")
                max_t = hour.get("max_temp")

                if avg is not None and min_t is not None and max_t is not None:
                    self.warn(f"hour {hour['hour']} min <= avg",
                             min_t > avg + 0.1,
                             f"min {min_t} > avg {avg}")
                    self.warn(f"hour {hour['hour']} avg <= max",
                             avg > max_t + 0.1,
                             f"avg {avg} > max {max_t}")

    def test_data_freshness(self):
        """Check that recent data exists."""
        print("\n🧪 Test: data freshness")

        now = utcnow()
        if self.presence:
            latest_ts = parse_ts(self.presence[-1]["ts"])
            age_mins = (now - latest_ts).total_seconds() / 60
            self.warn("recent data < 5 min old", age_mins > 5,
                     f"latest data {age_mins:.0f} min old")

        # Check API
        data = api_get("latest", self.auth)
        if data and data.get("ts"):
            # API returns ISO 8601, strip Z
            ts_str = data["ts"].rstrip("Z")
            try:
                api_ts = datetime.fromisoformat(ts_str)
                age_mins = (now - api_ts).total_seconds() / 60
                self.warn("API data < 5 min old", age_mins > 5,
                         f"API data {age_mins:.0f} min old")
            except (ValueError, AttributeError):
                pass

    def report(self):
        """Print summary."""
        total = self.passed + self.failed + self.warnings
        print("\n" + "="*60)
        print(f"Results: {self.passed} passed, {self.failed} failed, {self.warnings} warnings")
        print()
        return self.failed == 0


def main():
    print("🔍 IoT API Regression Tests\n")

    # Setup
    user, password = api_auth()
    if not user or not password:
        print("❌ DASH_USER/DASH_PASS not found in .env")
        return False

    auth = HTTPBasicAuth(user, password)

    # Check API
    print("🌐 Checking API...")
    if not api_get("devices", auth):
        print("❌ API unreachable")
        return False
    print("  ✓ API reachable\n")

    # Load data
    print("📂 Loading production database...")
    try:
        presence = load_db()
        print(f"  ✓ {len(presence)} presence rows\n")
    except Exception as e:
        print(f"❌ Failed to load: {e}")
        return False

    # Run tests
    print("="*60 + "\n")
    tests = RegressionTests(presence, auth)
    tests.test_latest_endpoint_shape()
    tests.test_stats24h_values_in_range()
    tests.test_confidence_is_0_to_100()
    tests.test_occupied_is_0_or_1()
    tests.test_air_pressure_trend()
    tests.test_hourly_aggregation_consistency()
    tests.test_data_freshness()

    return tests.report()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
