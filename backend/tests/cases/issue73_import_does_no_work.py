# #73: importing server.py does no work; start() does it all.
import os, sys, tempfile, threading
from unittest import mock
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for name in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging",
             "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[name] = mock.MagicMock()
sys.modules["firebase_admin"].get_app.side_effect = ValueError("no app")
sys.path.insert(0, backend)
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
threads_before = threading.active_count()
import server
import state, store, ingest, api
paho = server.mqtt   # the module object server.py imported
fa = sys.modules["firebase_admin"]
check("import: no database file", not os.path.exists("data.db"))
check("import: no MQTT client", state.mqttc is None and not paho.Client.called)
check("import: no Firebase", not fa.initialize_app.called)
check("import: no threads", threading.active_count() == threads_before)
check("health before start: 503, not a crash", api.app.test_client().get("/api/health").status_code == 503)

server.start()
check("start: database created", os.path.exists("data.db") and store.get_state("occupancy_armed") == "0")
check("start: MQTT connected and looping", paho.Client.called and state.mqttc.connect.called and state.mqttc.loop_start.called)
check("start: callbacks set", state.mqttc.on_message is ingest.on_message and state.mqttc.on_connect is ingest.on_connect)
check("start: Firebase initialised", fa.initialize_app.call_count == 1)
check("start: 2 background threads", threading.active_count() == threads_before + 2)

# db() closes the connection also when a query raises
import sqlite3
closed = []
real_connect = sqlite3.connect
class Conn:
    def __init__(self, *a): self.c = real_connect(*a)
    def execute(self, *a): return self.c.execute(*a)
    def commit(self): self.c.commit()
    def close(self): closed.append(1); self.c.close()
with mock.patch.object(store.sqlite3, "connect", Conn):
    try:
        with store.db() as conn:
            conn.execute("SELECT * FROM no_such_table")
    except sqlite3.OperationalError:
        pass
check("db(): closed after an error", closed == [1])
