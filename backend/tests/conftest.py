"""Shared setup for the backend tests (#124).

Each test gets the `env` fixture: its own temp directory as the working directory
(so `data.db` is fresh), fake credentials in the environment, stubbed Firebase and
paho, and no backend module imported yet. The test imports what it needs
(`import server, ingest, ...`) and calls `server.prepare()`; since #73 importing does
no work, so it can set up an old database first where a migration is tested.
Afterwards the environment, sys.modules and logging are put back, so tests don't
leak into each other.
"""
import logging
import os
import sys
from unittest import mock

import pytest

from support import BACKEND

BACKEND_MODULES = ("server", "config", "state", "store", "alerts", "nodes", "server_checks", "ingest", "api")
STUBBED = ("firebase_admin", "firebase_admin.credentials", "firebase_admin.messaging",
           "paho", "paho.mqtt", "paho.mqtt.client")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _forget_backend():
    for name in BACKEND_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    saved_env = dict(os.environ)
    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)

    monkeypatch.chdir(tmp_path)
    os.environ.update(DASH_USER="u", DASH_PASS="p", MQTT_USER="m", MQTT_PASS="m")
    for name in STUBBED:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    _forget_backend()

    yield tmp_path

    _forget_backend()
    os.environ.clear()
    os.environ.update(saved_env)
    for name in BACKEND_MODULES:   # handlers / propagate a test set on a module logger
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(logging.NOTSET)
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
