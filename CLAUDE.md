# Project overview
Home sensor system. ESP32 nodes (Arduino C++) publish readings over MQTT.
A Flask backend subscribes, stores data in SQLite and sends push
notifications via Firebase. A small web page and an Android app display the data.

# Layout
- /firmware  Arduino sketches: main_node (radar, BME280, CO2), eink_node; shared code in
             firmware/libraries/HomePing (an Arduino library)
- /backend   Flask app: server.py (entry point), config, state, store (SQLite,
             creates the DB schema on start), alerts, nodes,
             server_checks (watchdog pushes), ingest (MQTT), air (weather,
             window/CO₂ observation), api; tools/ has validation scripts against the
             live DB and API (README there)
- /web       HTML page (index.html + app.js + i18n.js for EN/HU, no inline scripts: CSP), connects to MQTT
             directly from the browser; nginx/ has the site's security headers
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
  `journalctl -u iot-backend -f`), as user `iot` from `/opt/iot` with its own venv,
  served by waitress on 127.0.0.1:5000 behind nginx (backend/systemd/).
- Backend (local dev/tests): `python server.py` with a local .env and a temp SQLite file
- Test push (on the server, in /opt/iot): `sudo -u iot venv/bin/python test_push.py ["Title" "Text"]`
- Validation (on the server, in /opt/iot, never as root): `sudo -u iot venv/bin/python tools/validate_db.py`
  (also validate_db_deep, validate_api, api_regressions; backend/tools/README.md)
- Android: `./gradlew assembleDebug` in /android
- Firmware: Arduino IDE, board ESP32 Dev Module, Partition Scheme "Minimal SPIFFS (1.9MB APP
  with OTA)"; first flash over USB, then over WiFi (Tools > Port > the node's network port,
  password `OTA_PASS` from secrets.h, #79). The nodes connect to the broker with TLS on 8883
  (`MQTT_BROKER` = the certificate's host name, #31). CI compiles both sketches with
  arduino-cli and the same partition scheme (.github/workflows/firmware.yml).