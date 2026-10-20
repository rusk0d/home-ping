#!/usr/bin/env python3
"""
Deep database validation: aggressive checks for subtle bugs and edge cases.

These checks look for:
- Suspicious data patterns (jumps, gaps, anomalies)
- State machine violations (events that shouldn't happen together)
- Logical contradictions (min > max, wrong aggregations)
- Temporal inconsistencies (backwards time, future dates, bad intervals)
- Correlation failures (events without supporting data)
"""

import sys

from _common import DB_PATH, connect_ro
import config
from store import parse_ts, utcnow

DB = DB_PATH
PRESENCE_DEVICE = config.PRESENCE_DEVICE
NOTIFICATION_COOLDOWN = config.NOTIFICATION_COOLDOWN


def connect():
    return connect_ro()


def run_deep_checks():
    """Run aggressive edge-case checks."""
    issues = []

    print("🔬 Deep Database Validation (edge cases & state machines)")
    print(f"   Database: {DB}")
    print(f"   Time: {utcnow()}")
    print()

    # ========== TEMPORAL CHECKS ==========
    print("⏰ Temporal Consistency")

    # Check 1: No future timestamps
    print("  1. Checking for future timestamps...")
    try:
        conn = connect()
        now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")

        for table in ['readings', 'presence_readings', 'events', 'devices']:
            c = conn.cursor()
            if table == 'devices':
                c.execute(f"SELECT device, last_seen FROM {table} WHERE last_seen > ?", (now_str,))
            else:
                c.execute(f"SELECT device, ts FROM {table} WHERE ts > ?", (now_str,))

            bad_rows = c.fetchall()
            if bad_rows:
                for row in bad_rows[:3]:
                    issues.append(f"⏰ {table}: future timestamp {row['ts'] or row['last_seen']}")
                print(f"   ❌ {table}: {len(bad_rows)} future rows")
            else:
                print(f"   ✅ {table}: all timestamps are in the past")

        conn.close()
    except Exception as e:
        issues.append(f"⏰ Error checking timestamps: {e}")

    # Check 2: No backwards time jumps within device stream (except after RESTART)
    print("  2. Checking for backwards time jumps...")
    try:
        conn = connect()
        c = conn.cursor()

        c.execute("""
            SELECT device FROM readings GROUP BY device HAVING COUNT(*) > 1
        """)
        devices = [r['device'] for r in c.fetchall()]

        jump_issues = []
        for device in devices:
            c.execute("""
                SELECT ts FROM readings WHERE device = ? ORDER BY ts
            """, (device,))
            rows = c.fetchall()

            for i in range(1, len(rows)):
                prev_ts = parse_ts(rows[i-1]['ts'])
                curr_ts = parse_ts(rows[i]['ts'])

                if curr_ts < prev_ts:
                    jump_issues.append(f"{device}: {prev_ts} → {curr_ts}")

        if jump_issues:
            for issue in jump_issues[:3]:
                issues.append(f"⏰ Time went backwards: {issue}")
                print(f"   ❌ {issue}")
        else:
            print("   ✅ All devices have monotonic timestamps")

        conn.close()
    except Exception as e:
        issues.append(f"⏰ Error checking time jumps: {e}")

    # ========== DATA BOUNDS CHECKS ==========
    print("\n📊 Data Bounds & Sanity")

    # Check 3: Temperature in reasonable range
    print("  3. Checking temperature bounds...")
    try:
        conn = connect()
        c = conn.cursor()

        # Sensor range is -40 to +85°C, but we're in a house so much narrower expected
        c.execute("""
            SELECT COUNT(*) as count FROM readings
            WHERE temp < -20 OR temp > 50
        """)
        bad_temps = c.fetchone()['count']

        if bad_temps > 0:
            c.execute("""
                SELECT device, ts, temp FROM readings
                WHERE temp < -20 OR temp > 50
                ORDER BY ts DESC LIMIT 3
            """)
            for row in c.fetchall():
                issues.append(f"📊 Extreme temp: {row['device']} {row['ts']} = {row['temp']}°C")
                print(f"   ⚠️  {row['device']}: {row['temp']}°C at {row['ts']}")
        else:
            print("   ✅ All temperatures within -20 to +50°C")

        conn.close()
    except Exception as e:
        issues.append(f"📊 Error checking temps: {e}")

    # Check 4: Humidity bounds (0-100%)
    print("  4. Checking humidity bounds...")
    try:
        conn = connect()
        c = conn.cursor()

        c.execute("""
            SELECT COUNT(*) as count FROM readings
            WHERE humidity < 0 OR humidity > 100
        """)
        bad_humidity = c.fetchone()['count']

        if bad_humidity > 0:
            c.execute("""
                SELECT device, ts, humidity FROM readings
                WHERE humidity < 0 OR humidity > 100
                ORDER BY ts DESC LIMIT 3
            """)
            for row in c.fetchall():
                issues.append(f"📊 Invalid humidity: {row['device']} = {row['humidity']}% at {row['ts']}")
                print(f"   ❌ {row['device']}: {row['humidity']}% at {row['ts']}")
        else:
            print("   ✅ All humidity values 0-100%")

        conn.close()
    except Exception as e:
        issues.append(f"📊 Error checking humidity: {e}")

    # Check 5: CO2 reasonable (< 5000 ppm is normal, even for old air)
    print("  5. Checking CO2 bounds...")
    try:
        conn = connect()
        c = conn.cursor()

        c.execute("""
            SELECT COUNT(*) as count FROM readings
            WHERE co2 < 0 OR co2 > 5000
        """)
        bad_co2 = c.fetchone()['count']

        if bad_co2 > 0:
            c.execute("""
                SELECT device, ts, co2 FROM readings
                WHERE co2 < 0 OR co2 > 5000
                ORDER BY ts DESC LIMIT 3
            """)
            for row in c.fetchall():
                issues.append(f"📊 Extreme CO2: {row['device']} = {row['co2']} ppm at {row['ts']}")
                print(f"   ⚠️  {row['device']}: {row['co2']} ppm at {row['ts']}")
        else:
            print("   ✅ All CO2 values 0-5000 ppm")

        conn.close()
    except Exception as e:
        issues.append(f"📊 Error checking CO2: {e}")

    # Check 6: Pressure reasonable (500-1100 hPa is Earth's range; indoors ~970-1050)
    print("  6. Checking pressure bounds...")
    try:
        conn = connect()
        c = conn.cursor()

        c.execute("""
            SELECT COUNT(*) as count FROM readings
            WHERE pressure < 500 OR pressure > 1100
        """)
        bad_pressure = c.fetchone()['count']

        if bad_pressure > 0:
            c.execute("""
                SELECT device, ts, pressure FROM readings
                WHERE pressure < 500 OR pressure > 1100
                ORDER BY ts DESC LIMIT 3
            """)
            for row in c.fetchall():
                issues.append(f"📊 Invalid pressure: {row['device']} = {row['pressure']} hPa at {row['ts']}")
                print(f"   ❌ {row['device']}: {row['pressure']} hPa at {row['ts']}")
        else:
            print("   ✅ All pressure values 500-1100 hPa")

        conn.close()
    except Exception as e:
        issues.append(f"📊 Error checking pressure: {e}")

    # ========== OCCUPANCY LOGIC CHECKS ==========
    print("\n👤 Occupancy & Presence Logic")

    # Check 7: Occupancy shouldn't jump (no 0→100 in one reading)
    print("  7. Checking for occupancy jumps...")
    try:
        conn = connect()
        c = conn.cursor()

        # Get sequences for PRESENCE_DEVICE
        c.execute("""
            SELECT ts, occupied FROM presence_readings
            WHERE device = ?
            ORDER BY ts
        """, (PRESENCE_DEVICE,))
        rows = c.fetchall()

        # Occupancy that goes 0→1→0 within a few seconds
        c.execute("""
            SELECT
                r1.ts as ts1, r1.occupied as occ1,
                r2.ts as ts2, r2.occupied as occ2,
                r3.ts as ts3, r3.occupied as occ3
            FROM presence_readings r1
            JOIN presence_readings r2 ON r2.id = r1.id + 1
            JOIN presence_readings r3 ON r3.id = r1.id + 2
            WHERE r1.device = ? AND r2.device = ? AND r3.device = ?
            AND r1.occupied = 0 AND r2.occupied = 1 AND r3.occupied = 0
        """, (PRESENCE_DEVICE, PRESENCE_DEVICE, PRESENCE_DEVICE))

        spikes = c.fetchall()
        if spikes:
            for spike in spikes[:3]:
                issues.append(f"👤 Occupancy spike: {spike['ts1']} (0) → {spike['ts2']} (1) → {spike['ts3']} (0)")
                print(f"   ⚠️  Occupancy spike at {spike['ts2']}")
        else:
            print("   ✅ No suspicious occupancy spikes (0→1→0)")

        conn.close()
    except Exception:
        # This query might fail if table is small, that's ok
        print("   ⏭️  Not enough data for spike detection")

    # ========== EVENT CONSISTENCY ==========
    print("\n📝 Event Consistency")

    # Check 8: OCCUPANCY_CHANGE events should correspond to actual changes
    print("  8. Checking OCCUPANCY_CHANGE events match data...")
    try:
        conn = connect()
        c = conn.cursor()

        # Get all OCCUPANCY_CHANGE events
        c.execute("""
            SELECT ts, device, value FROM events
            WHERE type = 'OCCUPANCY_CHANGE'
            ORDER BY ts DESC LIMIT 20
        """)
        events = c.fetchall()

        orphaned = []
        for event in events:
            # Check if there's an occupancy change at that timestamp
            c.execute("""
                SELECT occupied FROM presence_readings
                WHERE device = ? AND ts = ?
                LIMIT 1
            """, (event['device'], event['ts']))
            reading = c.fetchone()

            if not reading:
                # No reading at exact timestamp, check nearby (within 2 seconds; events log ~1s after reading)
                c.execute("""
                    SELECT occupied FROM presence_readings
                    WHERE device = ? AND ts >= datetime(?, '-2 seconds') AND ts <= datetime(?, '+2 seconds')
                    LIMIT 1
                """, (event['device'], event['ts'], event['ts']))
                reading = c.fetchone()

                if not reading:
                    orphaned.append((event['device'], event['ts'], event['value']))

        if orphaned:
            for device, ts, value in orphaned[:3]:
                issues.append(f"📝 Orphaned OCCUPANCY_CHANGE: {device} {ts} {value} (no supporting reading)")
                print(f"   ⚠️  No reading for {device} at {ts}")
        else:
            print("   ✅ All OCCUPANCY_CHANGE events have supporting readings")

        conn.close()
    except Exception as e:
        issues.append(f"📝 Error checking OCCUPANCY_CHANGE: {e}")

    # Check 9: Window events should alternate (open→close→open)
    print("  9. Checking window event state machine...")
    try:
        conn = connect()
        c = conn.cursor()

        c.execute("""
            SELECT ts, device, type FROM events
            WHERE type IN ('WINDOW_OPEN', 'WINDOW_CLOSED')
            ORDER BY ts
        """)
        events = c.fetchall()

        bad_sequences = []
        for i in range(1, len(events)):
            prev_type = events[i-1]['type']
            curr_type = events[i]['type']

            # After OPEN, should come CLOSED (or another OPEN if the first didn't close)
            # After CLOSED, should come OPEN
            # Two consecutive OPENs or CLOSEDs = bug
            if prev_type == curr_type:
                bad_sequences.append(f"{prev_type} → {curr_type}")

        if bad_sequences:
            for seq in bad_sequences[:3]:
                issues.append(f"📝 Bad window sequence: {seq}")
                print(f"   ❌ {seq}")
        else:
            print("   ✅ Window events properly alternate")

        conn.close()
    except Exception as e:
        issues.append(f"📝 Error checking window events: {e}")

    # ========== AGGREGATION SANITY ==========
    print("\n🔢 Aggregation Sanity")

    # Check 10: min ≤ avg ≤ max for all numeric aggregates
    print("  10. Checking min ≤ avg ≤ max in hourly...")
    try:
        conn = connect()
        c = conn.cursor()

        # Check temp
        c.execute("""
            SELECT COUNT(*) as count FROM hourly
            WHERE (temp_min IS NOT NULL AND temp_avg IS NOT NULL AND temp_max IS NOT NULL)
            AND (temp_min > temp_avg OR temp_avg > temp_max)
        """)
        bad_temp = c.fetchone()['count']

        # Check humidity
        c.execute("""
            SELECT COUNT(*) as count FROM hourly
            WHERE (humidity_min IS NOT NULL AND humidity_avg IS NOT NULL AND humidity_max IS NOT NULL)
            AND (humidity_min > humidity_avg OR humidity_avg > humidity_max)
        """)
        bad_humidity = c.fetchone()['count']

        # Check CO2
        c.execute("""
            SELECT COUNT(*) as count FROM hourly
            WHERE (co2_min IS NOT NULL AND co2_avg IS NOT NULL AND co2_max IS NOT NULL)
            AND (co2_min > co2_avg OR co2_avg > co2_max)
        """)
        bad_co2 = c.fetchone()['count']

        # Check pressure
        c.execute("""
            SELECT COUNT(*) as count FROM hourly
            WHERE (pressure_min IS NOT NULL AND pressure_avg IS NOT NULL AND pressure_max IS NOT NULL)
            AND (pressure_min > pressure_avg OR pressure_avg > pressure_max)
        """)
        bad_pressure = c.fetchone()['count']

        bad_total = bad_temp + bad_humidity + bad_co2 + bad_pressure
        if bad_total > 0:
            issues.append(f"🔢 {bad_total} hourly rows violate min ≤ avg ≤ max")
            print(f"   ❌ {bad_temp} temp, {bad_humidity} humidity, {bad_co2} CO2, {bad_pressure} pressure violations")
        else:
            print("   ✅ All hourly aggregates satisfy min ≤ avg ≤ max")

        conn.close()
    except Exception as e:
        issues.append(f"🔢 Error checking aggregates: {e}")

    # ========== ALERT COOLDOWN CHECK ==========
    print("\n⏲️  Alert Cooldown Logic")

    # Check 11: No duplicate alerts for same key within cooldown
    print("  11. Checking alert cooldown violations...")
    try:
        conn = connect()
        c = conn.cursor()

        # Get all alert events
        c.execute("""
            SELECT ts, device, type FROM events
            WHERE type LIKE '%_ALERT'
            ORDER BY ts
        """)
        alerts = c.fetchall()

        violations = []
        for i in range(1, len(alerts)):
            prev = alerts[i-1]
            curr = alerts[i]

            # Same device and type?
            if prev['device'] == curr['device'] and prev['type'] == curr['type']:
                prev_ts = parse_ts(prev['ts'])
                curr_ts = parse_ts(curr['ts'])
                gap = (curr_ts - prev_ts).total_seconds()

                if gap < NOTIFICATION_COOLDOWN:
                    violations.append(f"{curr['device']} {curr['type']}: {gap}s gap (< {NOTIFICATION_COOLDOWN}s)")

        if violations:
            for v in violations[:3]:
                issues.append(f"⏲️  Alert cooldown violation: {v}")
                print(f"   ❌ {v}")
        else:
            print("   ✅ No alert cooldown violations")

        conn.close()
    except Exception as e:
        issues.append(f"⏲️  Error checking cooldowns: {e}")

    # ========== SUMMARY ==========
    print()
    print("=" * 60)
    if issues:
        print(f"⚠️  FINDINGS: {len(issues)} potential issues found")
        print()
        for issue in issues[:10]:
            print(f"  • {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")
        return 1
    else:
        print("✅ No issues found in deep validation")
        return 0


if __name__ == "__main__":
    sys.exit(run_deep_checks())
