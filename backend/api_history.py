"""REST API, history from the hourly table: /api/hourly, /api/daily. A Flask blueprint (#306);
contracts/api.md.
"""

import logging

from flask import Blueprint, jsonify, request

from config import (
    DAILY_MAX_DAYS, HOURLY_MAX_DAYS,
    ROOM_DEVICES_SQL,
)
from store import (
    db,
)
from ingest import hourly_occupancy_today
from api_common import require_auth

log = logging.getLogger(__name__)
bp = Blueprint("history", __name__)


@bp.route("/api/hourly")
@require_auth
def hourly():
    # Temperature/humidity from one device if ?device= is given, else from whichever
    # device reports them (the BME280 moved boards in the node merge, #57)
    device = request.args.get("device")
    # days=1: today; days=N: today and the N-1 days before it (history screen, #82).
    # Today comes from the raw readings, the days before from the hourly table (#173):
    # raw readings are kept only RAW_RETENTION_DAYS. ?device= applies to today only.
    days = request.args.get("days", "1")
    if not (days.isascii() and days.isdigit()) or not 1 <= int(days) <= HOURLY_MAX_DAYS:
        return jsonify({"error": f"days must be 1-{HOURLY_MAX_DAYS}"}), 400
    days = int(days)
    since = "+0 days"   # raw part: since local midnight

    with db() as conn:
        c = conn.cursor()
        # Past days: hourly is per UTC hour; grouping by the local label merges the
        # repeated hour of the autumn DST change, like the raw queries below
        c.execute("""
            SELECT strftime('%Y-%m-%d %H:00', hour, 'localtime'),
                   AVG(temp_avg), AVG(humidity_avg), AVG(co2_avg), AVG(pressure_avg), AVG(occupancy)
            FROM hourly
            WHERE hour >= datetime('now','localtime','start of day',?,'utc')
              AND hour < datetime('now','localtime','start of day','utc')
            GROUP BY 1
        """, (f"-{days - 1} days",))
        past_rows = {r[0]: r[1:] for r in c.fetchall()}
        c.execute(f"""
            SELECT
                strftime('%Y-%m-%d %H:00', ts, 'localtime'),
                AVG(temp),
                AVG(humidity)
            FROM readings
            WHERE (device = ? OR (? IS NULL AND {ROOM_DEVICES_SQL})) AND (temp IS NOT NULL OR humidity IS NOT NULL)
              AND ts >= datetime('now','localtime','start of day',?,'utc')
            GROUP BY 1
        """, (device, device, since))
        env_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}
        # CO2 and pressure from whichever room device reports them (like /api/air)
        c.execute(f"""
            SELECT
                strftime('%Y-%m-%d %H:00', ts, 'localtime'),
                AVG(co2),
                AVG(pressure)
            FROM readings
            WHERE (co2 IS NOT NULL OR pressure IS NOT NULL) AND {ROOM_DEVICES_SQL}
              AND ts >= datetime('now','localtime','start of day',?,'utc')
            GROUP BY 1
        """, (since,))
        air_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}
    occ_rows = hourly_occupancy_today()

    hours = sorted(set(env_rows) | set(air_rows) | set(occ_rows) | set(past_rows), reverse=True)
    out = []
    for h in hours:
        if h in past_rows:
            t, hu, co2, pr, o = past_rows[h]
        else:
            t, hu = env_rows.get(h, (None, None))
            co2, pr = air_rows.get(h, (None, None))
            o = occ_rows.get(h)
        out.append({
            "hour": h,
            "avg_temp": round(t, 1) if t is not None else None,
            "avg_humidity": round(hu, 1) if hu is not None else None,
            "avg_co2": round(co2) if co2 is not None else None,
            "avg_pressure": round(pr, 1) if pr is not None else None,
            "avg_occupancy": round(o, 0) if o is not None else None,
        })
    return jsonify(out)


@bp.route("/api/daily")
@require_auth
def daily():
    """One entry per local day from the hourly table (#129), newest first: long ranges
    beyond the raw readings (kept RAW_RETENTION_DAYS). Today has only its complete hours."""
    days = request.args.get("days", "30")
    if not (days.isascii() and days.isdigit()) or not 1 <= int(days) <= DAILY_MAX_DAYS:
        return jsonify({"error": f"days must be 1-{DAILY_MAX_DAYS}"}), 400
    with db() as conn:
        rows = conn.execute("""
            SELECT date(hour, 'localtime'),
                   AVG(temp_avg), MIN(temp_min), MAX(temp_max),
                   AVG(humidity_avg), MIN(humidity_min), MAX(humidity_max),
                   AVG(co2_avg), MAX(co2_max), AVG(pressure_avg),
                   SUM(occupancy) / 100, COUNT(*)
            FROM hourly
            WHERE hour >= datetime('now','localtime','start of day',?,'utc')
            GROUP BY 1 ORDER BY 1 DESC
        """, (f"-{int(days) - 1} days",)).fetchall()

    def r(v, digits=1):
        return None if v is None else round(v, digits)
    return jsonify([{
        "date": d,
        "avg_temp": r(t), "min_temp": r(tmin), "max_temp": r(tmax),
        "avg_humidity": r(hu), "min_humidity": r(humin), "max_humidity": r(humax),
        "avg_co2": None if co2 is None else round(co2), "max_co2": co2max,
        "avg_pressure": r(pr),
        "occupied_hours": r(occ),
        "hours": n,
    } for d, t, tmin, tmax, hu, humin, humax, co2, co2max, pr, occ, n in rows])
