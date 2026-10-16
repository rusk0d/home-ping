"""#58: a bad payload must not escape on_message (paho would re-raise it and end its network thread).
"""

import paho.mqtt.client as real_paho          # the real library, for its dispatch code
from support import BACKEND, check


def test_issue58_bad_payload(env):
    backend = str(BACKEND)
    import os, sys, json, tempfile, sqlite3, types
    from unittest import mock
    logged = []
    import logging
    class _Log(logging.Handler):   # errors are logged since #66
        def emit(self, r): logged.append(r.getMessage())
    logging.getLogger("ingest").addHandler(_Log(level=logging.ERROR))
    logging.getLogger("ingest").propagate = False
    import server
    import config, ingest
    server.prepare()   # importing does no work (#73)

    client = real_paho.Client(real_paho.CallbackAPIVersion.VERSION2)   # same as the backend (#66)
    check("paho re-raises callback errors by default", client.suppress_exceptions is False)
    client.on_message = ingest.on_message
    def deliver(topic, payload):
        m = real_paho.MQTTMessage(topic=topic.encode()); m.payload = payload
        client._handle_on_message(m)          # paho's own dispatch path
    rows = lambda: sqlite3.connect(config.DB).execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    for label, topic, payload in [
        ("invalid UTF-8 on a reading topic", "home/esp32/bme280", b"\xff\xfe"),
        ("invalid UTF-8 on a status topic", "home/esp32/x/status", b"\xffonline"),
        ("not JSON", "home/esp32/bme280", b"hello"),
        ("JSON but not an object", "home/esp32/bme280", b"[1,2,3]"),
        ("empty payload", "home/esp32/co2", b""),
    ]:
        try:
            deliver(topic, payload); check(f"{label}: handled inside the callback", True)
        except Exception as e:
            check(f"{label}: escaped to paho ({type(e).__name__})", False)
    check("errors are logged", any(l.startswith("MQTT error on ") for l in logged))
    check("no row stored from bad payloads", rows() == 0)
    deliver("home/esp32/bme280", json.dumps({"device": "home_ping_esp32", "temp_c": 22.0, "humidity": 45.0, "uptime": 5}).encode())
    check("next valid message is still stored", rows() == 1)
