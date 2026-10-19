"""#245 (observe-only): window openings from the CO₂/temperature readings, CO₂ excursions
while someone is home, and the Open-Meteo weather. Events only, no pushes.
"""

import io
import json
from unittest import mock

from support import check


def test_issue245_air_observe(env):
    import sqlite3
    import time
    from datetime import timedelta
    import server
    import config, store, air
    server.prepare()

    t0 = store.utcnow().replace(microsecond=0) - timedelta(hours=3)

    def readings(start, values, step=30):
        """(temp, humidity, co2) every [step] s from [start]; returns the time of the last one."""
        conn = sqlite3.connect(config.DB)
        for i, (t, h, c) in enumerate(values):
            ts = start + timedelta(seconds=i * step)
            conn.execute("INSERT INTO readings (device, ts, temp, humidity, co2, co2_ok) VALUES (?, ?, ?, ?, ?, ?)",
                         (config.AIR_DEVICE, ts.strftime("%Y-%m-%d %H:%M:%S"), t, h, c, None if c is None else 1))
        conn.commit()
        return start + timedelta(seconds=(len(values) - 1) * step)

    def events(kind):
        conn = sqlite3.connect(config.DB)
        return [v for (v,) in conn.execute("SELECT value FROM events WHERE type = ? ORDER BY id", (kind,))]

    def run(start, values):
        """Feed the readings one by one, checking after each, like the 60 s loop does."""
        for i in range(len(values)):
            air.check_air(readings(start + timedelta(seconds=i * 30), values[i:i + 1]))

    # This morning's 07:30 airing, simplified: steady, a fast fall, then the temperature back
    steady = [(23.9, 50, 1580)] * 20
    falling = [(23.9 - 0.15 * i, 50, 1580 - 100 * i) for i in range(1, 11)]   # -1.5 °C, -1000 ppm
    open_ = [(22.4, 52, 560)] * 6
    closing = [(22.4 + 0.1 * i, 51, 560 + 15 * i) for i in range(1, 11)]   # +1.0 °C
    run(t0, steady + falling)
    opened = events("WINDOW_OPEN")
    check("a fast fall opens a window spell, once", len(opened) == 1 and "no weather" in opened[0])
    check("...remembering the temperature from before", store.get_state("air:window", "").endswith("|23.9"))
    check("...starting where the fall started, not when it crossed the threshold",
          store.get_state("air:window", "").startswith((t0 + timedelta(seconds=19 * 30)).strftime("%Y-%m-%d %H:%M:%S")))
    run(t0 + timedelta(seconds=30 * 30), open_ + closing)
    closed = events("WINDOW_CLOSED")
    check("the temperature climbing back closes it", len(closed) == 1 and "temperature rising" in closed[0])
    check("...and only one opening was seen", len(events("WINDOW_OPEN")) == 1 and store.get_state("air:window", "x") == "")

    # Open with CO₂ still rising while the temperature keeps falling (the terrace door, 2026-10-18)
    t1 = t0 + timedelta(minutes=40)
    run(t1, [(23.0, 50, 1400)] * 20 + [(23.0 - 0.08 * i, 50, 1400 - 100 * i) for i in range(1, 11)])
    run(t1 + timedelta(minutes=15), [(22.2 - 0.02 * i, 50, 400 + 15 * i) for i in range(10)])
    check("CO₂ rising alone doesn't close a temperature-triggered spell",
          len(events("WINDOW_OPEN")) == 2 and len(events("WINDOW_CLOSED")) == 1)
    air.check_air()   # real now: the readings above are hours old
    check("no recent readings: no verdict", len(events("WINDOW_CLOSED")) == 1)

    # Stale or empty data never opens anything
    store.set_state("air:window", "")
    check("no rows: nothing", air.check_air(t0 - timedelta(days=1)) is None and len(events("WINDOW_OPEN")) == 2)

    # CO₂ excursion while someone is home (radar occupied just now)
    store.set_state("air:window", ""); store.set_state("air:window_closed", "")
    conn = sqlite3.connect(config.DB)
    conn.execute("DELETE FROM readings"); conn.commit()
    conn.execute("INSERT INTO presence_readings (device, ts, occupied) VALUES (?, datetime('now'), 1)",
                 (config.PRESENCE_DEVICE,))
    conn.commit()
    t2 = store.utcnow().replace(microsecond=0) - timedelta(minutes=30)
    run(t2, [(23.0, 50, 1250)] * 25)
    high = events("AIR_HIGH")
    check("the window scenarios above were high too, nobody home then",
          len(high) == 3 and all(h.endswith("nobody home: no push") for h in high[:2]))
    check("CO₂ ≥ 1200 for 10 min, someone home: AIR_HIGH once", high[2].endswith("would push"))
    oks = len(events("AIR_OK"))
    run(t2 + timedelta(seconds=25 * 30), [(23.0, 50, 1000)] * 5)
    check("1000 ppm: same excursion", len(events("AIR_HIGH")) == 3 and len(events("AIR_OK")) == oks)
    run(t2 + timedelta(seconds=30 * 30), [(23.0, 50, 850)])
    check("below 900: AIR_OK", len(events("AIR_OK")) == oks + 1)

    # The node sends CO₂ and temperature/humidity as separate messages (separate rows): the
    # verdict still compares the indoor air with outside
    conn.execute("DELETE FROM readings"); conn.commit()
    store.set_state("air:co2_high", "")
    air.WEATHER = {"at": time.time(), "temp": 24.0, "humidity": 90, "precipitation": 0}
    t3 = store.utcnow().replace(microsecond=0) - timedelta(minutes=12)
    conn = sqlite3.connect(config.DB)
    for i in range(24):
        ts = (t3 + timedelta(seconds=i * 30)).strftime("%Y-%m-%d %H:%M:%S")
        if i % 3:
            conn.execute("INSERT INTO readings (device, ts, temp, humidity) VALUES (?, ?, 23.0, 50)",
                         (config.AIR_DEVICE, ts))
        else:
            conn.execute("INSERT INTO readings (device, ts, co2, co2_ok) VALUES (?, ?, 1300, 1)",
                         (config.AIR_DEVICE, ts))
    conn.commit()
    air.check_air(t3 + timedelta(seconds=23 * 30))
    check("CO₂ and temperature in separate rows: humidity still compared",
          "would hold back (outside" in events("AIR_HIGH")[-1])
    air.WEATHER = None

    # Weather: parsing, the verdicts, and the "no coordinates = no fetch" default
    body = json.dumps({"current": {"temperature_2m": 9.8, "relative_humidity_2m": 80, "precipitation": 0.0}})
    with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(body.encode())) as get:
        w = air.fetch_weather()
        check("Open-Meteo current values", (w["temp"], w["humidity"], w["precipitation"]) == (9.8, 80.0, 0.0)
              and "api.open-meteo.com" in get.call_args.args[0].full_url)
        air.update_weather()
        check("no WEATHER_LAT/LON: no fetch", get.call_count == 1 and air.weather() is None)
    air.WEATHER = dict(w)
    check("weather in the text", air.outside_text(air.weather()) == "outside 9.8 °C 80 %")
    check("cold outside air is drier: would push", air.air_verdict(23.0, 50) == "would push")
    air.WEATHER["precipitation"] = 0.4
    check("raining: held back", air.air_verdict(23.0, 50) == "would hold back (raining)")
    air.WEATHER.update(temp=24.0, humidity=90, precipitation=0)
    check("muggy outside: held back", air.air_verdict(23.0, 50).startswith("would hold back (outside"))
    air.WEATHER["at"] = time.time() - config.WEATHER_MAX_AGE - 1
    check("stale weather counts as none", air.weather() is None)
    check("absolute humidity", abs(air.absolute_humidity(22, 50) - 9.7) < 0.1
          and abs(air.absolute_humidity(5, 90) - 6.1) < 0.1)
