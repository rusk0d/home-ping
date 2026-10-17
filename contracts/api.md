# REST API contract

The REST API between the backend (`backend/api.py`) and the Android app.
It is the source of truth: update it in the same change as the code (see CLAUDE.md).
First written on 2026-10-13 by reading the code; anything still marked **UNCERTAIN** was
inferred then and hasn't been confirmed since.

## General

- Server: Flask app served by waitress (since #59) on `127.0.0.1:5000` (`BIND_HOST`/`PORT` in
  `.env`), behind nginx with TLS, which proxies `/api/`. Production runs under systemd as the
  unprivileged user `iot` from `/opt/iot` (`backend/systemd/iot-backend.service`).
- Auth: HTTP Basic. One shared account, `DASH_USER` / `DASH_PASS` from
  `backend/.env`. On failure: `401`, body `Authentication required`
  (text/plain), header `WWW-Authenticate: Basic realm="Login Required"`.
  Every endpoint uses the `require_auth` decorator, except `/api/health` (#77).
  The user and password are compared in constant time (#61). On the server, fail2ban bans an
  IP for 1 h after 10 failed logins (401) on `/api/` within 10 min (`backend/fail2ban/`).
- Content type: `application/json` (via `jsonify`) unless noted.
- Timestamps: **ISO 8601 in UTC**, `"YYYY-MM-DDTHH:MM:SSZ"` (since #65; before that,
  server-local `"YYYY-MM-DD HH:MM:SS"` without a zone). They are the time the backend
  received the message, not the device's measurement time. Exception: the `hour` labels of
  `/api/hourly` are Budapest (server) local time, because they describe "today".
- "online": `now - devices.last_seen < HEARTBEAT_TIMEOUT` (**120 s**).
- Responses are JSON objects or arrays of objects; no positional arrays (since #14).
- No data: values are `null` (status 200), never `0`, `[]` or 404 (since #14).
- "confidence" (`confidence`, per radar or combined) always means: % of a radar's rows in the
  last **15 min** (`CONFIDENCE_MINUTES`) that were occupied, 0–100, or null without radar
  data. Combined = the highest radar. Other occupancy figures are called "occupancy".
- Default device: many endpoints take `?device=` and default to `"home_ping_esp32"`.
- Errors: JSON `{"error": "<message>"}` with the HTTP status (400 bad request body,
  404 unknown path, 405 wrong method, 413 body over 16 KB (`MAX_CONTENT_LENGTH`, #123),
  500 unexpected error without details). The only
  exception is the 401 from Basic auth (text/plain, see Auth).
- CORS: none configured. The web page does not use the REST API.

## Clients

| Client | Endpoints used |
|---|---|
| Android `HomeFragment` | `/api/dashboard` (since #128; falls back to `/api/latest_all`, `/api/devices`, `/api/stats24h_combined`, `/api/restarts`, `/api/air`, `/api/hourly`), `POST /api/occupancy_arm`, `/api/occupancy_status` |
| Android `MainActivity` (FCM) | none since #153 (registers with FCM itself) |
| Android `HistoryActivity` | `/api/hourly?days=7` |
| Android `NotificationSettingsActivity` | `GET`/`POST /api/notification_settings` |
| Android `HomeEnvWidget` | `/api/latest`, `/api/air` |
| Web (`web/index.html`) | none (MQTT only) |
| Firmware | none (eink_node's unused `API_URL` config was removed in #16) |

## Endpoint index

| Method | Path | Auth | Used by |
|---|---|---|---|
| GET | `/api/latest` | Basic | Android widget |
| GET | `/api/latest_all` | Basic | Android Home |
| GET | `/api/stats24h_combined` | Basic | Android Home |
| GET | `/api/hourly` | Basic | Android Home, History |
| GET | `/api/daily` | Basic | Android History (#129) |
| GET | `/api/air` | Basic | Android Home, widget |
| GET | `/api/devices` | Basic | Android Home |
| GET | `/api/restarts` | Basic | Android Home |
| POST | `/api/occupancy_arm` | Basic | Android Home |
| GET | `/api/occupancy_status` | Basic | Android Home |
| GET, POST | `/api/notification_settings` | Basic | Android notification settings |
| GET | `/api/health` | **none** | external uptime monitor (#77) |
| GET | `/api/dashboard` | Basic | Android Home (#128) |

---

## Endpoints

### GET `/api/dashboard` (since #128)

Everything the Android home screen shows, in one response: the six endpoints below, each
under its own name and with exactly its shape (the backend calls their own functions).

```json
{"latest_all": {...}, "devices": [...], "stats24h_combined": {...},
 "restarts": {...}, "air": {...}, "hourly": [...]}
```

| Key | Same as |
|---|---|
| `latest_all` | `GET /api/latest_all` |
| `devices` | `GET /api/devices` |
| `stats24h_combined` | `GET /api/stats24h_combined` |
| `restarts` | `GET /api/restarts` |
| `air` | `GET /api/air` |
| `hourly` | `GET /api/hourly` (no parameters: today, temperature/humidity from any device) |

Query parameters on `/api/dashboard` are ignored: each part runs without them (#170).

Client: Android Home, every refresh. If it fails (e.g. an older backend without it, 404), the
app falls back to the six requests. The single endpoints stay for the widget, the history
screen and that fallback.

### GET `/api/latest`

Latest temperature/humidity plus combined radar occupancy (the widget's data). Since the node
merge (#57) the env values come from the newest `readings` row of **whichever device** has
`temp` or `humidity` (the BME280 moved boards), like `/api/air`.
Always 200; fields are null when there is no data.

```json
{"ts":"2026-10-13T17:42:10Z","temp":22.4,"humidity":48.3,"uptime":86400,
 "online":true,"occupied":1,"confidence":73}
```

| Field | Type | Source |
|---|---|---|
| `ts` | string/null | that row (newest with temp or humidity, any device) |
| `temp` | number/null | °C |
| `humidity` | number/null | %RH |
| `uptime` | integer/null | s |
| `online` | bool | heartbeat of the device that row came from |
| `occupied` | 0/1/null | `get_combined_occupancy()`: 1 if any radar has ≥2 of its last 3 rows (within 2 min) occupied, null if no radar reported in 2 min |
| `confidence` | integer 0–100/null | `calculate_combined_confidence()` (see "confidence" above) |

Client: `HomeEnvWidget` reads `temp`, `humidity`, `occupied`, `confidence`, `online`.

### GET `/api/latest_all`

Latest state of every device, keyed by device ID, plus a `combined` entry.

For each device with `readings` rows:

```json
{"ts": "...", "uptime": 86400, "online": true,
 "temp": 22.4, "humidity": 48.3, "pressure": 1013.6, "env_ts": "...",
 "co2": null, "co2_ok": null, "co2_ts": null}
```

A node can send env and CO₂ in separate messages (since #57 the CO₂ board also has the
BME280), so each group comes from its own newest row (#11):
- `ts`, `uptime`: the device's newest `readings` row of any kind.
- `temp`, `humidity`, `pressure`, `env_ts`: its newest row with any of the three.
- `co2`, `co2_ok`, `co2_ts`: its newest row with `co2` or `co2_ok`. `co2_ok` is a bool if
  that message had `co2_ok` (survives backend restarts), else null; `co2` is null while the
  sensor warms up or reads invalid.
- A group the device never sent is all null.

For each device with `presence_readings` rows, the radar fields are merged in.
If the device has no env rows, a stub with `temp`/`humidity`/`co2`/`pressure` = null is used.
Then **`ts` and `uptime` are overwritten** with the radar row's values:

```json
{"ts": "...", "uptime": 86395, "occupied": 1, "confidence": 73,
 "online": true, "presence": 1, "stationary": 1, "moving": 0,
 "distance": 142, "signal": 37, "rssi": -61, "radar_connected": 1}
```

Radar booleans are integers 0/1 here (as stored in SQLite).
`confidence` = `calculate_confidence(device)` (see "confidence" above).

Plus:

```json
"combined": {"occupied": 1, "confidence": 73}
```

`combined` is the same as `occupied`/`confidence` in `/api/latest` (both null without recent radar data).

Full example (abridged):

```json
{
  "home_ping_esp32": {"ts":"2026-10-13T17:42:15Z","temp":22.4,"humidity":48.3,"uptime":86405,"co2":null,"pressure":1013.6,"online":true,"co2_ok":null,"occupied":1,"confidence":73,"presence":1,"stationary":1,"moving":0,"distance":142,"signal":37,"rssi":-61,"radar_connected":1},
  "hallway_presence_esp32": {"temp":null,"humidity":null,"co2":null,"pressure":null,"ts":"2026-10-13T17:42:13Z","uptime":3605,"occupied":0,"confidence":0,"online":true,"presence":0,"stationary":0,"moving":0,"distance":0,"signal":0,"rssi":-70,"radar_connected":1},
  "co2_sensor_esp32": {"ts":"2026-10-13T17:41:58Z","temp":null,"humidity":null,"uptime":7260,"co2":812,"pressure":null,"online":true,"co2_ok":true},
  "combined": {"occupied":1,"confidence":73}
}
```

Client: Android Home reads temperature/humidity (+ `env_ts`) from the entry with the newest
`env_ts`, and `co2`, `co2_ok` (+ `co2_ts`) from the entry with the newest `co2_ts` (since #57/#11:
the main node after the merge, `co2_sensor_esp32` before; falls back to those IDs if the backend
has no `env_ts`/`co2_ts`). Plus `uptime` per device for the device list (the retired
`co2_sensor_esp32` only while `/api/devices` lists it) and `combined.{occupied, confidence}`.

### GET `/api/stats24h_combined`

24 h averages over **all** devices' env data plus radar occupancy stats. No params.

```json
{"avg_temp": 22.1, "avg_humidity": 47.9, "avg_occupancy": 41.0,
 "occupied_hours": 9.84, "coverage_hours": 24.0}
```

- `avg_temp`/`avg_humidity`: mean over all `readings` rows in the last 24 h with both non-null,
  rounded to 1 dp. null when there are no rows.
- `avg_occupancy`: % (0–100, rounded to 0 dp) from `occupancy_stats(24)`. The highest-occupancy radar wins.
- `occupied_hours`: that radar's span × occupied fraction (2 dp).
- `coverage_hours`: longest per-radar span of data in the window (1 dp).
- The three occupancy fields are null without radar data in the window.

Client: Android Home reads `avg_temp` (double), `avg_humidity` (int),
`avg_occupancy` (int), `occupied_hours` (double).

### GET `/api/hourly?device=<id>&days=<n>`

Per-hour buckets **since local (Budapest) midnight** (not a rolling 24 h), newest first.
`days` (optional, integer 1–7, default 1): `1` = today only; `n` = today and the `n-1`
days before it (since #82). Readings are kept 8 days, so 7 is the maximum. Anything
else → 400 `{"error": "days must be 1-7"}`.
`hour` is a local-time label (`"YYYY-MM-DD HH:00"`, no zone). On the autumn DST change the
repeated hour 02:00–03:00 falls into one bucket.
Temperature/humidity averages come from `readings` of `device` if given, else of any device
that reports them (since #57; before: default `home_ping_esp32`).
CO2/pressure averages come from `readings` of any device (like `/api/air`).
Occupancy comes from `presence_readings` for `home_ping_esp32` only (`PRESENCE_DEVICE`).
An hour appears if any of these has data.

```json
[{"hour":"2026-10-13 19:00","avg_temp":22.3,"avg_humidity":48.0,"avg_co2":812,
  "avg_pressure":1013.4,"avg_occupancy":62.0}]
```

- `avg_temp`/`avg_humidity`: number or null (1 dp).
- `avg_co2`: integer ppm or null (since #82).
- `avg_pressure`: hPa, number or null (1 dp, since #82).
- `avg_occupancy`: mean(`occupied`)×100, rounded to 0 dp. null if no radar rows that hour.

Clients:
- Android Home occupancy bar strip and sparklines (`days` omitted; reads `hour`,
  `avg_occupancy`, `avg_temp`, `avg_humidity`; reverses the order). A null
  `avg_occupancy` is drawn as "no data".
- Android History (`days=7`): one line chart per metric over 7 × 24 hourly slots (a
  missing hour or null value breaks the line), and occupied hours per day
  = sum(`avg_occupancy`) / 100.

### GET `/api/daily` (since #129)

One entry per **server-local day** from the `hourly` table (see `db-schema.md`), newest
first. For ranges beyond the 8 days of raw readings that `/api/hourly` is limited to.

- `?days=N`: today and the N-1 days before it, 1–400 (`DAILY_MAX_DAYS`), default 30.
  Anything else → `400 {"error": "days must be 1-400"}`.
- Only days with at least one stored hour appear. Today has only its complete hours.
- Day values are over its stored hours: averages are the mean of the hourly averages (each
  hour weighs the same), min/max the extremes of the hourly min/max.

```json
[{"date":"2026-10-16","avg_temp":22.6,"min_temp":21.8,"max_temp":24.1,
  "avg_humidity":41.3,"min_humidity":34.0,"max_humidity":48.2,
  "avg_co2":712,"max_co2":1180,"avg_pressure":1011.4,"occupied_hours":8.9,"hours":20}]
```

| Field | Type | Notes |
|---|---|---|
| `date` | string | `YYYY-MM-DD`, server local date |
| `avg_temp` / `min_temp` / `max_temp` | number or null | °C, 1 dp |
| `avg_humidity` / `min_humidity` / `max_humidity` | number or null | %, 1 dp |
| `avg_co2` | integer or null | ppm |
| `max_co2` | integer or null | ppm |
| `avg_pressure` | number or null | hPa, 1 dp |
| `occupied_hours` | number or null | sum of the hourly occupancy % / 100 (like the History screen's occupancy bars), 1 dp; null = no radar data that day |
| `hours` | integer | stored hours that day (24 for a full day) |

### GET `/api/air`

Latest CO2 and pressure from whichever device last reported them.

```json
{"ts":"2026-10-13T17:42:10Z","co2":812,"band":"fair","pressure":1013.6,
 "pressure_3h_change":-1.2,"temp":22.4,"humidity":48.3,"mould_risk":false}
```

- `co2`: integer ppm or null. Latest non-null `readings.co2` from any device.
- `band`: `"good"` (<800), `"fair"` (<1200), `"stuffy"` (<2000), `"bad"` (≥2000), null when there is no CO2 reading (see "Level bands" below). Android uses it for the CO₂ chip, falling back to its own copy of the bands.
- `mould_risk` (bool, since #177): true when **each of the last 3 complete hours**
  (`MOULD_HOURS`) averaged above **60 %RH** (`MOULD_HUMIDITY`, the top of the "Comfortable"
  band), over all devices' readings. An hour without humidity readings makes it false.
  Android shows a hint on the humidity card. The web page applies the same rule to
  `home/backend/history_24h` (its last 3 hours before `updated`).

### Level bands

The one place the level thresholds are defined (#127). Change them here first, then in
every copy: `backend/api.py` (`/api/air` `band`, CO₂ only), `web/app.js`
(`co2Level`, `humLevel`, `tempLevel`) and `android/.../ui/Levels.kt` (chips and gauge stops).

| Value | Bands (label, from low to high) |
|---|---|
| CO₂ (ppm) | Fresh `good` < 800 ≤ OK `fair` < 1200 ≤ Stuffy `stuffy` < 2000 ≤ Poor `bad` |
| Humidity (%) | Very dry < 30 ≤ Dry < 40 ≤ Comfortable ≤ 60 < Humid ≤ 70 < Very humid |
| Temperature (°C) | Cold < 18 ≤ Cool < 20 ≤ Comfortable ≤ 24 < Warm ≤ 27 < Hot |

Gauge ranges (drawing only): CO₂ 400–3000 ppm (web and Android), humidity 0–100 % and
temperature 14–30 °C (Android only).
- `pressure`/`temp`/`humidity`: from the latest row with non-null `pressure`.
- `pressure_3h_change`: hPa, latest pressure minus the newest reading at least 3 h old (1 dp), or null.
- `ts`: env row ts if present, else co2 row ts, else null.

Clients: Android Home reads only `pressure` and `pressure_3h_change` (its CO2 comes from `/api/latest_all`).
The widget reads `co2` for its CO2 row (fetched alongside `/api/latest`; `--` if this call fails).

### GET `/api/devices`

Every row in the `devices` table.

```json
[{"device":"home_ping_esp32","last_seen":"2026-10-13T17:42:15Z","online":true}]
```

Sensor nodes only. (Until #15, `/api/register_token` added an `"unknown"` row for
the phone; the backend deletes that row on start.)
Client: Android Home reads `device`, `online` for `home_ping_esp32`,
`hallway_presence_esp32`, `co2_sensor_esp32`.

### GET `/api/restarts`

Restart counts per device from `events` where `type='RESTART'`.

```json
{"co2_sensor_esp32":{"last_24h":1,"last_7d":3,"last_restart":"2026-10-13T05:12:40Z"}}
```

Devices with no restart ever are absent. Client: Android Home sums `last_24h`.

### POST `/api/occupancy_arm`

Request: `{"armed": true|false}`. `armed` must be a JSON boolean; a missing or
non-boolean `armed`, or a missing/non-JSON body, is a `400` and leaves the state unchanged.
Sets `system_state.occupancy_armed` to `'1'`/`'0'` and logs event
`OCCUPANCY_ARMED` (device `"system"`). Response: `{"armed": true|false}`.
Client: Android Home toggle switch.

### GET `/api/occupancy_status`

Current arm state for occupancy-arrival pushes, read from `system_state.occupancy_armed`.
Response: `{"armed": true|false}` (same shape as the `POST /api/occupancy_arm` response).
`false` until the switch is first armed.
Client: Android Home, on startup, to set the toggle switch.

### GET / POST `/api/notification_settings` (since #82)

Which alerts send a push, and quiet hours. Stored in `system_state` (see `db-schema.md`),
so the settings apply to every phone. Behaviour: `notifications.md` → "Settings".

```json
{"humidity":true,"temperature":true,"radar":true,"nodes":true,
 "quiet_hours":{"enabled":false,"start":"22:00","end":"07:00"}}
```

- `humidity`, `temperature`, `radar`, `nodes`: bool, default `true`. `false` mutes that alert type.
- `quiet_hours.enabled`: bool, default `false`. `start`/`end`: `"HH:MM"` (24 h, server
  local time = Budapest), defaults `22:00`/`07:00`. The window may cross midnight.
- Occupancy alerts are not in here: they keep their own switch (`/api/occupancy_arm`)
  and are never muted by quiet hours.

POST takes any subset of the GET fields (e.g. `{"quiet_hours":{"start":"23:00"}}`) and
returns the full settings, like GET. All fields are validated before anything is
written; a wrong type or a time that isn't `HH:MM` → 400 `{"error": ...}`. Each POST
that changes something logs a `NOTIFICATION_SETTINGS` event.

Client: Android notification settings screen (bell icon in the header). Each switch
or time change is one POST; on failure the screen goes back to the last confirmed state.

---

### GET `/api/health` (since #77)

For an external uptime monitor. **No auth**, so the monitor needs no password and failed
checks never count towards fail2ban. It returns no sensor data.

```json
{"ok":true,"mqtt_connected":true,"last_message_age_s":4,"db_ok":true}
```

| Field | Type | Meaning |
|---|---|---|
| `ok` | bool | all of the three checks below pass |
| `mqtt_connected` | bool | the backend's MQTT client is connected to the broker |
| `last_message_age_s` | int or null | seconds since the last live (non-retained) MQTT message; null before the first one since startup |
| `db_ok` | bool | `data.db` can be read |

Status **200** when `ok`, else **503** with the same body. Stale means no live message for
`HEALTH_MAX_MESSAGE_AGE` (**300 s**), counted from startup until the first message. The radars
publish every 5 s, so a stale feed means ingestion stopped (backend, broker or all nodes).
Client: an external monitor (UptimeRobot or similar) checking the status code.

## Mismatches and issues between components

1. **`ts`/`uptime` in `/api/latest_all` are silently overwritten** for devices that send both env
   and radar data (`home_ping_esp32`). The `ts` shown is the radar's, not the
   temperature reading's (#11).
2. **Duplicate FCM topic subscription.** Android subscribes itself to topic `env`
   client-side *and* the backend subscribes the token server-side. This is harmless but redundant.
3. **Hallway radar is invisible to the occupancy column of `/api/hourly`**, which only
   uses `home_ping_esp32` (#10).
4. **Android config.** The server base URL and Basic-auth credentials come from the
   gitignored `android/local.properties` (`API_BASE_URL`, `API_USER`, `API_PASS`)
   via `BuildConfig`. `API_BASE_URL` has no trailing slash and falls back to
   `https://www.example.com` if unset. Note that `BuildConfig` values are compiled
   into the APK and can be extracted from it (#60).

## Changelog

Fixed mismatches, oldest first (issue number: what changed).

- #3: `GET /api/occupancy_status` → `{"armed": bool}` added; Android reads it on startup.
- #9: soil feature removed (`/api/soil/*`, Android Plants tab), with its field-name,
  threshold and missing-data-source issues.
- #14: positional array responses replaced by objects (`/api/latest`); `/api/history_combined` removed.
- #14: always-0 `readings.occupied`/`occupancy_confidence` and `dark` dropped from responses and the table.
- #14: "confidence" means the 15-min value everywhere; the 60-min and hourly variants removed.
- #14: empty state is null with status 200 everywhere.
- #14: unused endpoints removed (`/api/latest2`, `/api/history`, `/api/stats24h`,
  `/api/occupancy_rolling`, `/api/presence/*`, `/api/widget`, `/api/next-events`, Google OAuth helper).
- #14: `HEARTBEAT_TIMEOUT` defined once (120 s).
- #15: `register_token` no longer adds the phone to `devices`; the old `"unknown"` row is deleted on start.
- Android no longer crashes on a null `temp`/`humidity` in `/api/latest_all`: it reads them
  null-safely (`optNum`) instead of `getDouble`/`getInt`.
- #129: `GET /api/daily`, long-term daily values from the new `hourly` table.
- #172: `POST /api/register_token` removed (legacy since #153; no app version that calls it is left).
