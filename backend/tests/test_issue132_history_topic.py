"""#132: retained home/backend/history_24h, hourly averages of the last 24 complete hours."""
import json
from datetime import timedelta

from support import check


def test_issue132_history_topic(env):
    import server, state, ingest
    from store import db, utcnow
    server.prepare()

    hour = utcnow().replace(minute=0, second=0, microsecond=0)   # start of the current hour
    def at(h_ago, minute):
        return (hour - timedelta(hours=h_ago) + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (at(0, 1), 30.0, 60.0, None, None),     # current hour: not complete, left out
        (at(1, 10), 22.0, 40.0, 1010.0, None),  # an hour ago: env + CO2 rows averaged
        (at(1, 40), 23.0, 42.0, 1012.0, None),
        (at(1, 20), None, None, None, 700),
        (at(1, 50), None, None, None, 801),
        (at(3, 5), 21.0, None, None, None),     # only temperature that hour
        (at(24, 0), 19.0, 50.0, 1000.0, 500),   # exactly 24 h before the current hour: in
        (at(25, 30), 18.0, 50.0, 1000.0, 500),  # older: out
    ]
    with db() as conn:
        conn.executemany("INSERT INTO readings (device, ts, temp, humidity, pressure, co2) VALUES ('home_ping_esp32', ?, ?, ?, ?, ?)", rows)

    h = ingest.history_24h()["hours"]
    iso = lambda h_ago: (hour - timedelta(hours=h_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    check("only complete hours of the last 24, oldest first", [x["start"] for x in h] == [iso(24), iso(3), iso(1)])
    check("averages, rounded", h[2] == {"start": iso(1), "temp": 22.5, "humidity": 41.0, "pressure": 1011.0, "co2": 750})
    check("missing values are null", h[1] == {"start": iso(3), "temp": 21.0, "humidity": None, "pressure": None, "co2": None})

    sent = []
    class FakeClient:
        def publish(self, topic, payload, qos=0, retain=False):
            sent.append((topic, json.loads(payload), retain))
    state.mqttc = FakeClient()
    ingest.publish_summaries()
    topics = {t: (p, r) for t, p, r in sent}
    check("both retained summaries published", set(topics) == {"home/backend/occupancy_hourly", "home/backend/history_24h"}
          and all(r for _, r in topics.values()))
    check("history payload", topics["home/backend/history_24h"][0]["hours"] == h)

    # One failing publish doesn't stop the other
    sent.clear()
    orig = ingest.publish_hourly_occupancy
    ingest.publish_hourly_occupancy = lambda: 1 / 0
    try:
        ingest.publish_summaries()
    finally:
        ingest.publish_hourly_occupancy = orig
    check("history still published", [t for t, _, _ in sent] == ["home/backend/history_24h"])
