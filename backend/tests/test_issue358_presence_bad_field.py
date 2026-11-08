"""#358: a bad numeric field in a presence message is 0; the rest of the message (the row, the
heartbeat) is kept instead of the whole message being dropped."""
import types

from support import check


def test_issue358_presence_bad_field(env):
    import server
    import ingest
    from store import db
    server.prepare()

    def mqtt(raw):
        ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/presence", payload=raw.encode()))

    def last():
        with db() as conn:
            return conn.execute("SELECT occupied, distance, signal, rssi, uptime FROM presence_readings "
                                "WHERE device = 'hallway_presence_esp32' ORDER BY id DESC LIMIT 1").fetchone()

    def seen():
        with db() as conn:
            return conn.execute("SELECT COUNT(*) FROM devices WHERE device = 'hallway_presence_esp32'").fetchone()[0]

    base = '"device": "hallway_presence_esp32", "presence": true, "moving": true, "uptime": 100'
    mqtt('{%s, "distance": 142, "signal": 37, "rssi": -61}' % base)
    check(f"a good message ({last()})", last() == (1, 142, 37, -61, 100))

    mqtt('{%s, "distance": "far", "signal": {}, "rssi": [1]}' % base)
    check(f"bad fields: 0, the row kept ({last()})", last() == (1, 0, 0, 0, 100) and seen() == 1)

    mqtt('{%s, "distance": Infinity, "signal": NaN, "rssi": -61.6}' % base)
    check(f"Infinity / NaN: 0, a float truncated ({last()})", last() == (1, 0, 0, -61, 100))

    mqtt('{%s, "distance": "120", "uptime": "x"}' % base)
    check(f"a numeric string still counts, a bad uptime is null ({last()})", last() == (1, 120, 0, 0, None))
