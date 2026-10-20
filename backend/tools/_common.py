"""Shared setup for the validation tools: the backend's modules, a read-only database
connection and API GETs with the login from .env.

Run the tools from the backend folder (/opt/iot on the server) as the service user, so
config.py finds .env and data.db: `sudo -u iot venv/bin/python tools/validate_db.py`.
Never as root: opening the WAL database as root can leave data.db-wal / data.db-shm owned
by root, and the backend (user iot) can't write until they're gone.
"""

import base64
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

if hasattr(os, "geteuid") and os.geteuid() == 0:
    sys.exit("Don't run the tools as root (see tools/_common.py): sudo -u iot venv/bin/python tools/<name>.py")

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config  # noqa: E402

DB_PATH = BACKEND / config.DB
API_BASE = f"http://{config.BIND_HOST}:{config.PORT}/api"


def connect_ro():
    """The database, read-only: the tools never write, and can't by accident."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def api_get(endpoint, timeout=5):
    """GET /api/<endpoint> with the .env login: the JSON of a 200 response, else None."""
    token = base64.b64encode(f"{config.AUTH_USER}:{config.AUTH_PASS}".encode()).decode()
    req = urllib.request.Request(f"{API_BASE}/{endpoint}", headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp) if resp.status == 200 else None
    except Exception:
        return None
