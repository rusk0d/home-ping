"""CO₂ outside the MH-Z19C range is stored as null on its own; the BME280 values in the
same payload are kept (they come from a different chip).
"""

import json
import logging
import sqlite3
import types

from support import check


def test_co2_range(env):
    import server
    import config, ingest
    server.prepare()
    warnings = []

    class _Warn(logging.Handler):
        def emit(self, r): warnings.append(r.getMessage())
    logging.getLogger("ingest").addHandler(_Warn(level=logging.WARNING))

    def mqtt(**payload):
        ingest.on_message(None, None, types.SimpleNamespace(
            retain=False, topic="home/esp32/co2", payload=json.dumps(payload).encode()))

    def last():
        return sqlite3.connect(config.DB).execute(
            "SELECT temp, humidity, co2, co2_ok FROM readings ORDER BY id DESC LIMIT 1").fetchone()

    mqtt(device="home_ping_esp32", co2=50000, co2_ok=True, temp_c=22.0, humidity=45.0)
    check("CO2 out of range: CO2 null, BME values kept", last() == (22.0, 45.0, None, 1))
    check("...with a warning, since the sender called it good", any("implausible CO2 50000" in w for w in warnings))

    warnings.clear()
    mqtt(device="home_ping_esp32", co2=-1, co2_ok=False)
    check("negative CO2 is null too", last()[2] is None)
    check("...but no warning when the sender said co2_ok=false (warm-up)", warnings == [])

    mqtt(device="home_ping_esp32", co2=5000, co2_ok=True)
    check("the range edge is kept", last()[2] == 5000)
    mqtt(device="home_ping_esp32", co2=420, co2_ok=True)
    check("a normal value is kept", last()[2] == 420)
