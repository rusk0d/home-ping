"""REST API helpers shared by the blueprints (#306): login, JSON, timestamps."""

import hmac
from functools import wraps

from flask import Response, abort, request

from config import AUTH_PASS, AUTH_USER, HEARTBEAT_TIMEOUT
from store import parse_ts, utcnow


def credentials_ok(user, password):
    """Constant-time check of both values, always both, so the timing doesn't tell
    whether the user name or the password was wrong (#61)."""
    user_ok = hmac.compare_digest((user or "").encode(), AUTH_USER.encode())
    pass_ok = hmac.compare_digest((password or "").encode(), AUTH_PASS.encode())
    return user_ok & pass_ok


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not credentials_ok(auth.username, auth.password):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Login Required"'}
            )
        return f(*args, **kwargs)
    return decorated


def is_online(last_seen, now=None):
    """A device with this devices.last_seen counts as online (#123: was three copies)."""
    return last_seen is not None and ((now or utcnow()) - parse_ts(last_seen)).total_seconds() < HEARTBEAT_TIMEOUT


def iso(ts):
    """Stored "YYYY-MM-DD HH:MM:SS" (UTC) -> ISO 8601 for the API: "YYYY-MM-DDTHH:MM:SSZ"."""
    return None if ts is None else ts.replace(" ", "T") + "Z"


def json_body():
    """The request's JSON object, or a 400 if the body is missing or not a JSON object."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="JSON object body required")
    return data
