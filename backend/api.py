"""REST API (contracts/api.md): the Flask app, its error handlers and /api/dashboard. The routes
are in blueprints by area (#306): api_env, api_history, api_notify, api_server; their helpers in
api_common.
"""

import logging

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

import api_env
import api_history
import api_notify
import api_server
from api_common import require_auth

log = logging.getLogger(__name__)

app = Flask(__name__)
# The JSON bodies are a few hundred bytes; waitress alone would accept up to 1 GB (#123).
# Larger requests get a JSON 413 (http_error below).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


@app.errorhandler(HTTPException)
def http_error(e):
    """API errors as JSON ({"error": ...}) instead of Flask's HTML pages."""
    return jsonify({"error": e.description}), e.code


@app.errorhandler(Exception)
def server_error(e):
    log.exception("%s: %r", request.path, e)
    return jsonify({"error": "internal error"}), 500


for blueprint in (api_env.bp, api_history.bp, api_notify.bp, api_server.bp):
    app.register_blueprint(blueprint)


@app.route("/api/dashboard")
@require_auth
def dashboard():
    """Everything the Android home screen shows, in one response (#128).

    Calls the six endpoints' own functions, so each part has exactly the shape (and
    defaults, e.g. /api/hourly = today) of the endpoint it is named after. Each runs in
    its own request context without a query string (#170), so /api/dashboard?days=7
    can't change a part.
    """
    def part(name, view):
        with app.test_request_context(f"/api/{name}"):
            rv = view.__wrapped__()   # the login was checked once, for /api/dashboard
        return (rv[0] if isinstance(rv, tuple) else rv).get_json()

    return jsonify({
        "latest_all": part("latest_all", api_env.latest_all),
        "devices": part("devices", api_env.devices),
        "stats24h_combined": part("stats24h_combined", api_env.stats24h_combined),
        "restarts": part("restarts", api_env.restarts),
        "air": part("air", api_env.air),
        "hourly": part("hourly", api_history.hourly),
    })
