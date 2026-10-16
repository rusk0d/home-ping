# #66: paho callback API v2 signatures, log levels.
import os, sys, json, tempfile, types, logging
from unittest import mock
import paho.mqtt.client as real_paho
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.packettypes import PacketTypes
backend = sys.argv[1]
os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m", LOG_LEVEL="warning")
saved = {k: sys.modules[k] for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
for n in ["firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging", "paho", "paho.mqtt", "paho.mqtt.client"]:
    sys.modules[n] = mock.MagicMock()
sys.path.insert(0, backend)
import server, config, ingest, state
server.prepare()
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)

records = []
class Cap(logging.Handler):
    def emit(self, r): records.append((r.levelname, r.name, r.getMessage()))
root = logging.getLogger(); root.addHandler(Cap()); root.setLevel(logging.DEBUG)

check("LOG_LEVEL from .env, upper-cased", config.LOG_LEVEL == "WARNING")
server.start()
check("client created with VERSION2", server.mqtt.Client.call_args.args == (server.mqtt.CallbackAPIVersion.VERSION2,))

# Real paho v2 dispatch: paho calls on_connect/on_disconnect with 5 arguments
ok = ReasonCode(PacketTypes.CONNACK, "Success")
bad = ReasonCode(PacketTypes.CONNACK, "Not authorized")
client = mock.MagicMock()
state.mqttc = mock.MagicMock()
ingest.on_connect(client, None, {}, ok, None)
check("connect ok: subscribes", client.subscribe.call_args.args == (config.TOPIC,))
check("connect ok: logged at INFO", ("INFO", "ingest", "MQTT connected") in records)
client.reset_mock()
ingest.on_connect(client, None, {}, bad, None)
check("connect refused: no subscribe", not client.subscribe.called)
check("connect refused: WARNING", any(l == "WARNING" and "refused" in m for l, n, m in records))
ingest.on_disconnect(client, None, real_paho.DisconnectFlags(False), ReasonCode(PacketTypes.DISCONNECT, "Unspecified error"), None)
check("disconnect: WARNING", any(l == "WARNING" and "disconnected" in m for l, n, m in records))

# Real paho client with our callbacks: v2 accepts them without the deprecation warning
import warnings
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    rc = real_paho.Client(real_paho.CallbackAPIVersion.VERSION2)
    rc.on_connect, rc.on_disconnect, rc.on_message = ingest.on_connect, ingest.on_disconnect, ingest.on_message
check("no DeprecationWarning with v2", not [x for x in w if issubclass(x.category, DeprecationWarning)])

# Levels: per-message lines are DEBUG, an alert is INFO
records.clear()
ingest.on_message(None, None, types.SimpleNamespace(retain=False, topic="home/esp32/presence",
    payload=json.dumps({"device": "d1", "presence": True, "moving": True, "signal": 50, "distance": 100}).encode()))
check("received/presence lines are DEBUG", records and all(l == "DEBUG" for l, n, m in records if "received" in m or m.startswith("presence")))
check("no INFO+ per message", not [r for r in records if r[0] != "DEBUG" and ("received" in r[2] or r[2].startswith("presence"))])
