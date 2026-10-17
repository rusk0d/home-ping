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
| `firmware/` | Arduino sketches: `main_node` (radar, BME280, MH-Z19 CO₂; merged with the former CO₂ node in #57), `eink_node` (hallway radar + e-ink display) |
| `backend/` | Flask app: `server.py` (entry point) with `config`, `store` (SQLite), `alerts`, `nodes`, `server_checks`, `ingest` (MQTT) and `api` modules; `test_push.py`, nightly `backup_db.py` |
| `web/` | Dashboard (`index.html` + `app.js`), connects to MQTT directly from the browser; `nginx/` has its security headers |
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

- **Firmware:** open the sketch in the Arduino IDE (board: ESP32 Dev Module, Tools >
  Partition Scheme: **Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)**) and upload. The
  first flash with that scheme goes over USB; after that, uploads can go over WiFi (#79):
  Tools > Port > the node's network port (`home-ping-esp32`, `hallway-presence-esp32`),
  password `OTA_PASS` from `secrets.h`. The PC must be on the home network.
  Keep the IDE's ESP32 core (Boards Manager > esp32) at the version CI pins in
  `.github/workflows/firmware.yml` (now 3.3.12), so CI checks what actually gets flashed;
  bump both together (#209).
  The sketches share code from `firmware/libraries/HomePing` (WiFi, MQTT with the status
  last will, uptime, radar presence). Make it visible to the IDE once by linking it into
  your Arduino libraries folder, e.g. on Windows (PowerShell):
  `New-Item -ItemType Junction -Path "$HOME\Documents\Arduino\libraries\HomePing" -Target "<repo>\firmware\libraries\HomePing"`.
  The link follows the repo, so a `git pull` updates it too.
- **Backend:** `pip install -r backend/requirements.txt`, then `python server.py` in
  `backend/` with a `.env` next to it (serves the API on 127.0.0.1:5000 with waitress).
  In production it runs as the systemd unit `iot-backend` as user `iot` from `/opt/iot`
  (see "Production setup"; `sudo systemctl restart iot-backend`, logs:
  `journalctl -u iot-backend -f`). Deploy: copy all `backend/*.py` to `/opt/iot`, then
  restart. Test a push with `sudo -u iot venv/bin/python test_push.py ["Title" "Text"]`
  in `/opt/iot`.
  Tests: `pip install -r backend/requirements-dev.txt`, then `pytest backend/tests` (no
  broker, Firebase or real config needed) and `ruff check backend` (lint, #175). CI runs them, compiles the firmware and builds
  the Android preview APK on PRs, each only when its folder changed
  (`.github/workflows/backend.yml`, `firmware.yml`, `android.yml`).
- **Web:** serve `web/` (with `config.js`, which holds only the broker URL) from any static
  web server. Each person signs in with their own read-only MQTT account; see
  `contracts/mqtt.md` → "Web users" for adding one. Deploy `index.html` and `app.js`
  together, and set up the security headers once (see "Web security headers").
- **Android:** `./gradlew assembleDebug` in `android/`. The `preview` build type
  (`./gradlew assemblePreview`) runs without Firebase.

## Backups

`backend/backup_db.py` copies `data.db` with SQLite's online backup API (safe while the
backend runs), checks the copy and keeps the newest 7 as `backups/data-YYYY-MM-DD.db.gz`.
A systemd timer runs it every night at 03:30. Install it on the server once (from the
backend directory, `/opt/iot`):

```
cp systemd/iot-backup.service systemd/iot-backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now iot-backup.timer
systemctl start iot-backup.service && ls -l backups/   # first backup right away
```

Check with `systemctl list-timers iot-backup.timer` and `journalctl -u iot-backup`.
The backend checks it too (#176): when the newest backup is over 26 h old, or less than
1 GB is free on the disk, the app gets a push (see `contracts/notifications.md`, "Server
watchdog").
To keep a copy off the server, pull the folder now and then, e.g.
`scp -r root@<server>:/opt/iot/backups ./iot-backups`.
Looking into the database by hand: `sudo -u iot sqlite3 /opt/iot/data.db`, not as root
(WAL mode, #174: root would create `data.db-shm`/`-wal` that the backend can't write).
Restore: `systemctl stop iot-backend`, `rm -f data.db-wal data.db-shm` (WAL files of the
old database, #174; normally already gone after a clean stop),
`gunzip -c backups/data-<date>.db.gz > data.db`, `chown iot:iot data.db`,
`systemctl start iot-backend`.

## Production setup

The backend runs as the unprivileged user `iot` from `/opt/iot`, in its own Python venv,
served by waitress on `127.0.0.1:5000`. nginx is the only public entry point and proxies
`/api/` to it. The unit (`backend/systemd/iot-backend.service`) also limits what the
process can write to `/opt/iot`.

Installing on a server (as root, one time):

```
apt install -y python3-venv                       # if "python3 -m venv" is missing
useradd --system --home-dir /opt/iot --shell /usr/sbin/nologin iot
mkdir -p /opt/iot
# copy backend/*.py, requirements.txt and systemd/ from the repo into /opt/iot, plus .env
# (from .env.example) and the Firebase service-account JSON
python3 -m venv /opt/iot/venv && /opt/iot/venv/bin/pip install -r /opt/iot/requirements.txt
chown -R iot:iot /opt/iot && chmod 750 /opt/iot && chmod 600 /opt/iot/.env /opt/iot/firebase_service_account.json
grep -rn proxy_pass /etc/nginx/sites-enabled/   # must point to 127.0.0.1:5000 (or localhost:5000)
cp /opt/iot/systemd/iot-backend.service /opt/iot/systemd/iot-backup.service /opt/iot/systemd/iot-backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now iot-backend iot-backup.timer
systemctl status iot-backend --no-pager; curl -s https://<your domain>/api/health
```

Both units run with `UMask=0077` (#169), so `data.db`, its WAL files and the backups are
readable only by `iot`.

### Ports

Public (ufw): 22 (SSH), 80/443 (nginx: the web page, `/api/` and `/mqtt`), 8883 (MQTT
over TLS for the nodes, #31). Everything else listens on `127.0.0.1` only: the backend on
5000 and Mosquitto's plain listener on 1883 (the backend). Mosquitto's WebSockets listener
on 9001 (nginx `/mqtt` is its only client) shows as `*:9001`, because this Mosquitto can't
bind websockets to localhost (#208); ufw is what keeps it closed. Check with `ss -ltnp` and
`ufw status`.

## Uptime monitoring

`GET /api/health` (no login, no sensor data) returns 200 while the backend is connected to
MQTT, gets live readings and can read the database, and 503 otherwise (see
`contracts/api.md`). Point a free external monitor at `https://<your domain>/api/health`,
e.g. UptimeRobot (HTTP(s) monitor, 5 min interval, alert by email or its phone app).
Because it runs off the server, it also notices when nginx or the whole VPS is down.

## Web security headers

`web/nginx/security-headers.conf` adds HSTS, `nosniff`, `Referrer-Policy`, a
Content-Security-Policy and hides the nginx version. The CSP allows scripts only from the
site itself and unpkg (mqtt.js, pinned with SRI), workers only from `blob:` (mqtt.js's
keep-alive timer, #203), and connections only to the broker. That
matters because "remember me" keeps the MQTT password in the browser's `localStorage`.
Install once (the `sed` fills in the broker host from the deployed `config.js`):

```
scp web/nginx/security-headers.conf root@<server>:/etc/nginx/snippets/home-ping-headers.conf
# on the server:
host=$(grep -o "wss://[^/']*" /var/www/<site>/config.js | cut -c7-); echo "$host"
sed -i "s/BROKER_HOST/$host/" /etc/nginx/snippets/home-ping-headers.conf
# add this line to the site's HTTPS server block (next to "root"):
#     include snippets/home-ping-headers.conf;
nginx -t && systemctl reload nginx
curl -sI https://<your domain>/ | grep -iE "^(server|content-security|strict)"
```

After changing the broker URL in `config.js`, run the `sed` step again on a fresh copy.
Then open the page with the browser's console open: it should connect, with no
"Refused to …" (CSP) messages.

## API brute-force protection

The API checks the login in constant time. Repeated wrong logins are blocked by
fail2ban on the server, which reads nginx's access log: 10 failed API logins (401)
within 10 minutes ban that IP for an hour. Install once, from the backend directory:

```
cp fail2ban/home-ping-api.conf /etc/fail2ban/filter.d/
cp fail2ban/home-ping-api.local /etc/fail2ban/jail.d/
grep access_log /etc/nginx/sites-enabled/*   # adjust logpath in jail.d/home-ping-api.local if needed
fail2ban-regex /var/log/nginx/access.log /etc/fail2ban/filter.d/home-ping-api.conf
systemctl reload fail2ban && fail2ban-client status home-ping-api
```

A ban covers the whole site (ports 80/443) for that IP. To lift one:
`fail2ban-client set home-ping-api unbanip <ip>`.
