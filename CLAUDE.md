# Project overview
Home sensor system. ESP32 nodes (Arduino C++) publish readings over MQTT.
A Flask backend subscribes, stores data in SQLite and sends push
notifications via Firebase. A small web page and an Android app display the data.

# Layout
- /firmware  Arduino sketches: co2_node, eink_node, main_node
- /backend   Flask app (server.py, creates the DB schema on start)
- /web       single-file HTML page, connects to MQTT directly from the browser
- /android   Kotlin app, talks only to the backend's REST API
- /contracts MQTT topics/payloads, REST API, DB schema (source of truth)
- /audit     read-only audit reports

# How the pieces connect
ESP32 -> MQTT broker -> Flask (SQLite) -> REST API -> Android app
Web page <- MQTT broker (WebSockets, one read-only user per person)
Backend -> Firebase Cloud Messaging -> Android push notifications

# Secrets and local-only files (never commit, never ask for the real values)
- firmware/*/secrets.h   (template: firmware/secrets.example.h)
- backend/.env           (template: backend/.env.example)
- backend Firebase service-account JSON, data.db
- web/config.js          (template: web/config.example.js)
- android/app/google-services.json (template: android/app/google-services.example.json)
- android/local.properties (template: android/local.properties.example; API URL and login)
When a build or test needs these, use the example files with fake values.

# Rules
- Audits are read-only: don't modify code, write findings to /audit.
- Don't change the MQTT topics, payload formats, REST endpoints or DB schema
  without updating /contracts in the same change.
- Keep changes small and focused, one component per change where possible.
- Don't add dependencies without saying why.
- Never hard-code credentials, IPs or tokens; use the config files above.

# Commands
- Backend (production): runs as a systemd service on the server
  unit `iot-backend` (`sudo systemctl status|restart iot-backend`, logs via
  `journalctl -u iot-backend -f`).
- Backend (local dev/tests): `python server.py` with a local .env and a temp SQLite file
- Test push (on the server, in the backend directory): `python3 test_push.py ["Title" "Text"]`
- Android: `./gradlew assembleDebug` in /android
- Firmware: compiled in the Arduino IDE (arduino-cli in CI later)