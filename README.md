# Home Ping

Home sensor system. ESP32 nodes measure temperature, humidity, pressure, CO₂ and
occupancy (LD2410C radar) and publish over MQTT. A Flask backend stores the data in
SQLite and sends push notifications; a web page and an Android app (with a
home-screen widget) show it.

## How the pieces connect

```
ESP32 nodes -> MQTT broker -> Flask backend (SQLite) -> REST API -> Android app + widget
Web page    <- MQTT broker (WebSockets, read-only user)
Backend     -> Firebase Cloud Messaging -> Android push notifications
```

## Layout

| Folder | What |
|---|---|
| `firmware/` | Arduino sketches: `co2_node`, `eink_node` (hallway radar + e-ink display), `main_node` |
| `backend/` | Flask app (`server.py`, creates the DB schema on start), `test_push.py` |
| `web/` | Single-file dashboard (`index.html`), connects to MQTT directly from the browser |
| `android/` | Kotlin app "Home Ping", talks only to the backend's REST API |
| `contracts/` | MQTT topics/payloads, REST API, push notifications, DB schema: the source of truth |
| `audit/` | Audit and code-review reports |

## Configuration

Secrets live in local files that are never committed. Copy the template and fill it in:

| Local file | Template |
|---|---|
| `firmware/<node>/secrets.h` | `firmware/secrets.example.h` |
| `backend/.env` | `backend/.env.example` |
| `web/config.js` | `web/config.example.js` |
| `android/local.properties` | `android/local.properties.example` (API URL and login) |
| `android/app/google-services.json` | `android/app/google-services.example.json` |

The backend also needs the Firebase service-account key as `firebase_service_account.json`
in its working directory.

## Build and run

- **Firmware:** open the sketch in the Arduino IDE (board: ESP32 Dev Module) and upload.
- **Backend:** `pip install -r backend/requirements.txt`, then `python server.py` in
  `backend/` with a `.env` next to it. In production it runs as the systemd unit
  `iot-backend` (`sudo systemctl restart iot-backend`, logs: `journalctl -u iot-backend -f`).
  Test a push with `python3 test_push.py ["Title" "Text"]`.
- **Web:** serve `web/` (with `config.js`, which holds only the broker URL) from any static
  web server. Each person signs in with their own read-only MQTT account; see
  `contracts/mqtt.md` → "Web users" for adding one.
- **Android:** `./gradlew assembleDebug` in `android/`. The `preview` build type
  (`./gradlew assemblePreview`) runs without Firebase.
