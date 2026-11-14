"""#68/#73: Firebase is initialised once, by start()/init_firebase(), not at import; with an
HTTP timeout (#449).
"""

from support import check


def test_issue68_firebase_init(env):
    import sys, types
    from unittest import mock
    fa = types.ModuleType("firebase_admin"); apps = []
    def get_app():
        if not apps: raise ValueError("The default Firebase app does not exist.")
        return apps[0]
    fa.get_app = get_app; fa.initialize_app = lambda cred, options=None: apps.append(options)
    fa.credentials = mock.MagicMock(); fa.messaging = mock.MagicMock()
    sys.modules.update({"firebase_admin": fa, "firebase_admin.credentials": fa.credentials, "firebase_admin.messaging": fa.messaging})
    import server
    check("import: Firebase not initialised", apps == [])
    server.init_firebase()
    check("no app yet -> initialised once", len(apps) == 1)
    check("with a short HTTP timeout, under the MQTT keep-alive (#449)", 0 < apps[0]["httpTimeout"] < 60)
    server.init_firebase()
    check("app exists -> not initialised again", len(apps) == 1)
