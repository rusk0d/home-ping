# #68/#73: Firebase is initialised once, by start()/init_firebase(), not at import.
import os, sys, types, tempfile
from unittest import mock
backend = sys.argv[1]; os.chdir(tempfile.mkdtemp())
os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
for n in ["paho", "paho.mqtt", "paho.mqtt.client"]: sys.modules[n] = mock.MagicMock()
fa = types.ModuleType("firebase_admin"); apps = []
def get_app():
    if not apps: raise ValueError("The default Firebase app does not exist.")
    return apps[0]
fa.get_app = get_app; fa.initialize_app = lambda cred: apps.append(cred)
fa.credentials = mock.MagicMock(); fa.messaging = mock.MagicMock()
sys.modules.update({"firebase_admin": fa, "firebase_admin.credentials": fa.credentials, "firebase_admin.messaging": fa.messaging})
sys.path.insert(0, backend)
def check(label, cond): print(("PASS " if cond else "FAIL ") + label)
import server
check("import: Firebase not initialised", apps == [])
server.init_firebase()
check("no app yet -> initialised once", len(apps) == 1)
server.init_firebase()
check("app exists -> not initialised again", len(apps) == 1)
