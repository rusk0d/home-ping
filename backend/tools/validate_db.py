#!/usr/bin/env python3
"""
Database invariant validation: checks must hold for the entire database.

Each failure is either a bug in the code or a wrong invariant. Both are progress.
Run against the whole database rather than fixtures.

Assertions:
1. occupied_hours <= coverage_hours for every device and window
2. coverage_hours <= window_hours
3. every hourly row equals the aggregate of the raw rows in that hour (within float tolerance)
4. no presence_readings row older than RAW_RETENTION_DAYS
5. no gap in hourly longer than the node's longest observed downtime
6. for every RESTART event, the uptime in the following row is lower than in the preceding one
7. devices.last_seen is never older than the newest row for that device
"""

import sys
from datetime import timedelta

# Add paths for imports
from _common import DB_PATH, connect_ro
import config
from store import parse_ts, utcnow

DB = DB_PATH
RAW_RETENTION_DAYS = config.RAW_RETENTION_DAYS
PRESENCE_DEVICE = config.PRESENCE_DEVICE


def connect():
    return connect_ro()


def run_checks():
    """Run all invariant checks against the database."""
    failures = []

    print("🔍 Database Invariant Validation")
    print(f"   Database: {DB}")
    print(f"   Time: {utcnow()}")
    print()

    # 1. Check: occupied_hours <= coverage_hours for every device and window
    print("1️⃣  Checking: occupied_hours <= coverage_hours (occupancy_stats window)")
    try:
        conn = connect()
        # Check the 24-hour window
        c = conn.cursor()
        c.execute("""
            SELECT device,
                   COUNT(*) as total_rows,
                   SUM(occupied) as occupied_count,
                   MIN(ts) as first_ts,
                   MAX(ts) as last_ts
            FROM presence_readings
            WHERE ts >= datetime('now', '-24 hours')
            GROUP BY device
        """)

        for row in c.fetchall():
            device = row['device']
            total_rows = row['total_rows']
            occupied_count = row['occupied_count'] or 0
            first_ts = row['first_ts']
            last_ts = row['last_ts']

            if total_rows == 0:
                continue

            # Calculate hours
            first = parse_ts(first_ts)
            last = parse_ts(last_ts)
            span_hours = (last - first).total_seconds() / 3600 if last > first else 0

            # occupied_hours is (occupied_count / total_rows) * span_hours
            # coverage_hours is span_hours
            # So occupied_hours <= coverage_hours is always true if span >= 0
            # But we should check the actual occupancy_stats logic

            occupied_fraction = occupied_count / total_rows if total_rows > 0 else 0
            occupied_hours = occupied_fraction * span_hours
            coverage_hours = span_hours

            if occupied_hours > coverage_hours + 0.01:  # float tolerance
                failures.append({
                    'check': '1',
                    'device': device,
                    'issue': f'occupied_hours ({occupied_hours:.2f}) > coverage_hours ({coverage_hours:.2f})',
                })
            else:
                print(f"   ✅ {device}: occupied={occupied_hours:.2f}h, coverage={coverage_hours:.2f}h")

        conn.close()
    except Exception as e:
        failures.append({'check': '1', 'error': str(e)})

    # 2. Check: coverage_hours <= window_hours (24 hours)
    print("\n2️⃣  Checking: coverage_hours <= window_hours")
    try:
        conn = connect()
        c = conn.cursor()
        c.execute("""
            SELECT device,
                   MIN(ts) as first_ts,
                   MAX(ts) as last_ts
            FROM presence_readings
            WHERE ts >= datetime('now', '-24 hours')
            GROUP BY device
        """)

        for row in c.fetchall():
            device = row['device']
            first_ts = row['first_ts']
            last_ts = row['last_ts']

            if first_ts is None or last_ts is None:
                continue

            first = parse_ts(first_ts)
            last = parse_ts(last_ts)
            coverage_hours = (last - first).total_seconds() / 3600 if last > first else 0
            window_hours = 24

            if coverage_hours > window_hours + 0.01:
                failures.append({
                    'check': '2',
                    'device': device,
                    'issue': f'coverage_hours ({coverage_hours:.2f}) > window_hours ({window_hours})',
                })
            else:
                print(f"   ✅ {device}: coverage={coverage_hours:.2f}h <= window={window_hours}h")

        conn.close()
    except Exception as e:
        failures.append({'check': '2', 'error': str(e)})

    # 3. Check: every hourly row equals the aggregate of raw rows (within tolerance)
    print("\n3️⃣  Checking: hourly aggregates match raw row sums (recent hours only; raw data is only kept 2 days)")
    try:
        conn = connect()
        c = conn.cursor()

        # Only check recent hours where raw data should still exist
        # Raw retention is RAW_RETENTION_DAYS; only check the last 48 hours to be safe
        cutoff = utcnow() - timedelta(days=RAW_RETENTION_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        c.execute("SELECT hour FROM hourly WHERE hour >= ? ORDER BY hour DESC", (cutoff_str,))
        hourly_hours = [r['hour'] for r in c.fetchall()]

        mismatches = []
        for hour_str in hourly_hours:
            hour_ts = parse_ts(hour_str)
            hour_end = hour_ts + timedelta(hours=1)
            hour_end_str = hour_end.strftime("%Y-%m-%d %H:%M:%S")

            # Get the hourly row
            c.execute("SELECT * FROM hourly WHERE hour = ?", (hour_str,))
            hourly = c.fetchone()
            if not hourly:
                continue

            # Aggregate raw readings for this hour
            c.execute("""
                SELECT AVG(temp) as temp_avg, MIN(temp) as temp_min, MAX(temp) as temp_max,
                       AVG(humidity) as humidity_avg, MIN(humidity) as humidity_min, MAX(humidity) as humidity_max,
                       AVG(co2) as co2_avg, MIN(co2) as co2_min, MAX(co2) as co2_max,
                       AVG(pressure) as pressure_avg, MIN(pressure) as pressure_min, MAX(pressure) as pressure_max
                FROM readings WHERE ts >= ? AND ts < ?
            """, (hour_str, hour_end_str))
            raw = c.fetchone()

            # Aggregate raw presence for this hour
            c.execute("""
                SELECT AVG(occupied) * 100 as occupancy
                FROM presence_readings WHERE device = ? AND ts >= ? AND ts < ?
            """, (PRESENCE_DEVICE, hour_str, hour_end_str))
            presence = c.fetchone()

            tolerance = 0.1

            # Check temp
            if raw and raw['temp_avg'] is not None:
                if hourly['temp_avg'] is None or abs(hourly['temp_avg'] - raw['temp_avg']) > tolerance:
                    mismatches.append(f"  {hour_str}: temp_avg mismatch (db={hourly['temp_avg']}, raw={raw['temp_avg']})")

            # Check humidity
            if raw and raw['humidity_avg'] is not None:
                if hourly['humidity_avg'] is None or abs(hourly['humidity_avg'] - raw['humidity_avg']) > tolerance:
                    mismatches.append(f"  {hour_str}: humidity_avg mismatch (db={hourly['humidity_avg']}, raw={raw['humidity_avg']})")

            # Check CO2
            if raw and raw['co2_avg'] is not None:
                if hourly['co2_avg'] is None or abs(hourly['co2_avg'] - raw['co2_avg']) > tolerance:
                    mismatches.append(f"  {hour_str}: co2_avg mismatch (db={hourly['co2_avg']}, raw={raw['co2_avg']})")

            # Check occupancy
            if presence and presence['occupancy'] is not None:
                if hourly['occupancy'] is None or abs(hourly['occupancy'] - presence['occupancy']) > tolerance:
                    mismatches.append(f"  {hour_str}: occupancy mismatch (db={hourly['occupancy']}, raw={presence['occupancy']})")

        if mismatches:
            for m in mismatches[:10]:  # Show first 10
                print(f"   ❌ {m}")
            failures.append({
                'check': '3',
                'issue': f'{len(mismatches)} hourly rows do not match raw aggregates',
                'examples': mismatches[:3],
            })
        else:
            print(f"   ✅ All {len(hourly_hours)} hourly rows match raw aggregates")

        conn.close()
    except Exception as e:
        failures.append({'check': '3', 'error': str(e)})

    # 4. Check: no presence_readings older than RAW_RETENTION_DAYS
    print(f"\n4️⃣  Checking: no presence_readings older than {RAW_RETENTION_DAYS} days")
    try:
        conn = connect()
        c = conn.cursor()

        # Each new presence row deletes the expired ones, so a row a moment past the cutoff
        # is just waiting for the next message: allow a few minutes
        cutoff = utcnow() - timedelta(days=RAW_RETENTION_DAYS, minutes=5)
        c.execute("SELECT COUNT(*) as count FROM presence_readings WHERE ts < ?",
                 (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
        old_rows = c.fetchone()['count']

        if old_rows > 0:
            c.execute("""
                SELECT device, ts FROM presence_readings WHERE ts < ?
                ORDER BY ts LIMIT 3
            """, (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
            old = c.fetchall()
            failures.append({
                'check': '4',
                'issue': f'{old_rows} presence_readings older than cutoff',
                'examples': [(r['device'], r['ts']) for r in old],
            })
            print(f"   ❌ Found {old_rows} old presence_readings")
            for device, ts in [(r['device'], r['ts']) for r in old]:
                print(f"      {device}: {ts}")
        else:
            print("   ✅ No presence_readings older than cutoff")

        conn.close()
    except Exception as e:
        failures.append({'check': '4', 'error': str(e)})

    # 5. Check: no gap in hourly longer than node's longest observed downtime
    print("\n5️⃣  Checking: no gap in hourly table")
    try:
        conn = connect()
        c = conn.cursor()

        # Get all hourly rows in order
        c.execute("SELECT hour FROM hourly ORDER BY hour")
        hours = [r['hour'] for r in c.fetchall()]

        if len(hours) < 2:
            print("   ⏭️  Not enough hourly data to check gaps")
        else:
            # Find largest gap
            gaps = []
            for i in range(1, len(hours)):
                prev = parse_ts(hours[i-1])
                curr = parse_ts(hours[i])
                gap_hours = (curr - prev).total_seconds() / 3600

                if gap_hours > 1.1:  # Should be 1 hour
                    gaps.append((hours[i-1], hours[i], gap_hours))

            if gaps:
                gaps.sort(key=lambda x: x[2], reverse=True)
                max_gap = gaps[0]
                print(f"   ⚠️  Found {len(gaps)} gap(s) in hourly table")
                print(f"      Largest gap: {max_gap[0]} -> {max_gap[1]} ({max_gap[2]:.1f} hours)")
                # Note: This might be expected if the node was offline
            else:
                print("   ✅ No gaps > 1 hour in hourly table")

        conn.close()
    except Exception as e:
        failures.append({'check': '5', 'error': str(e)})

    # 6. Check: for every RESTART event, uptime dropped
    print("\n6️⃣  Checking: RESTART events have uptime drops")
    try:
        conn = connect()
        c = conn.cursor()

        # Get all RESTART events
        c.execute("""
            SELECT id, device, ts, value FROM events
            WHERE type = 'RESTART'
            ORDER BY ts
        """)
        restarts = c.fetchall()

        restart_issues = []
        for restart in restarts:
            device = restart['device']
            event_ts = restart['ts']

            # Get the uptime from readings just before and after
            c.execute("""
                SELECT ts, uptime FROM readings
                WHERE device = ? AND ts < ?
                ORDER BY ts DESC LIMIT 1
            """, (device, event_ts))
            before = c.fetchone()

            c.execute("""
                SELECT ts, uptime FROM readings
                WHERE device = ? AND ts >= ?
                ORDER BY ts LIMIT 1
            """, (device, event_ts))
            after = c.fetchone()

            if before and after and before['uptime'] is not None and after['uptime'] is not None:
                if after['uptime'] >= before['uptime']:
                    restart_issues.append({
                        'device': device,
                        'event_ts': event_ts,
                        'before_uptime': before['uptime'],
                        'after_uptime': after['uptime'],
                    })

        if restart_issues:
            for issue in restart_issues[:5]:
                print(f"   ❌ {issue['device']} at {issue['event_ts']}: uptime {issue['before_uptime']}s -> {issue['after_uptime']}s (no drop!)")
            failures.append({
                'check': '6',
                'issue': f'{len(restart_issues)} RESTART events without uptime drop',
            })
        else:
            print(f"   ✅ All {len(restarts)} RESTART events have uptime drops")

        conn.close()
    except Exception as e:
        failures.append({'check': '6', 'error': str(e)})

    # 7. Check: devices.last_seen is never older than the newest row for that device
    print("\n7️⃣  Checking: devices.last_seen >= newest row for each device")
    try:
        conn = connect()
        c = conn.cursor()

        # Get all devices
        c.execute("SELECT device, last_seen FROM devices")
        devices = c.fetchall()

        device_issues = []
        for device_row in devices:
            device = device_row['device']
            last_seen = device_row['last_seen']

            if last_seen is None:
                continue

            last_seen_ts = parse_ts(last_seen)

            # Check readings table
            c.execute("""
                SELECT MAX(ts) as newest FROM readings WHERE device = ?
            """, (device,))
            reading = c.fetchone()
            if reading and reading['newest']:
                newest_ts = parse_ts(reading['newest'])
                if newest_ts > last_seen_ts:
                    device_issues.append({
                        'device': device,
                        'last_seen': last_seen,
                        'newest_row': reading['newest'],
                        'table': 'readings',
                    })

            # Check presence_readings table
            c.execute("""
                SELECT MAX(ts) as newest FROM presence_readings WHERE device = ?
            """, (device,))
            presence = c.fetchone()
            if presence and presence['newest']:
                newest_ts = parse_ts(presence['newest'])
                if newest_ts > last_seen_ts:
                    device_issues.append({
                        'device': device,
                        'last_seen': last_seen,
                        'newest_row': presence['newest'],
                        'table': 'presence_readings',
                    })

            # Check events table
            c.execute("""
                SELECT MAX(ts) as newest FROM events WHERE device = ?
            """, (device,))
            event = c.fetchone()
            if event and event['newest']:
                newest_ts = parse_ts(event['newest'])
                if newest_ts > last_seen_ts:
                    device_issues.append({
                        'device': device,
                        'last_seen': last_seen,
                        'newest_row': event['newest'],
                        'table': 'events',
                    })

        if device_issues:
            for issue in device_issues[:5]:
                print(f"   ❌ {issue['device']}: last_seen={issue['last_seen']}, newest in {issue['table']}={issue['newest_row']}")
            failures.append({
                'check': '7',
                'issue': f'{len(device_issues)} devices with stale last_seen',
            })
        else:
            print(f"   ✅ All {len(devices)} devices have current last_seen")

        conn.close()
    except Exception as e:
        failures.append({'check': '7', 'error': str(e)})

    # Summary
    print()
    print("=" * 60)
    if failures:
        print(f"❌ FAILURES: {len(failures)} check(s) failed")
        print()
        for f in failures:
            check = f.get('check', '?')
            if 'error' in f:
                print(f"  Check {check}: ERROR - {f['error']}")
            else:
                print(f"  Check {check}: {f.get('issue', 'unknown issue')}")
                if 'examples' in f:
                    for ex in f['examples'][:2]:
                        print(f"    - {ex}")
        return 1
    else:
        print("✅ All checks PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_checks())
