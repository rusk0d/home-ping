# #68: Firebase is initialised once, also when server.py is imported again.
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
import importlib, server
print(("PASS " if len(apps) == 1 else "FAIL ") + "no app yet -> initialised once")
importlib.reload(server)
print(("PASS " if len(apps) == 1 else "FAIL ") + "app exists -> not initialised again")
