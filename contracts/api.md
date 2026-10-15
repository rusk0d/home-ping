# REST API contract (DRAFT)

Status: draft, reverse-engineered from `backend/server.py` and the Android app
(`android/app/src/main/java/com/david/homeenv/*.kt`) on branch
`contracts-draft` (2026-10-13). Anything marked **UNCERTAIN** is inferred.

## General

- Server: Flask, `app.run(host="0.0.0.0", port=5000)` when run directly. Production runs under
  systemd. **UNCERTAIN**: Android uses `https://www.example.com/...`
  placeholders, so a reverse proxy with TLS presumably sits in front.
- Auth: HTTP Basic. One shared account, `DASH_USER` / `DASH_PASS` from
  `backend/.env`. On failure: `401`, body `Authentication required`
  (text/plain), header `WWW-Authenticate: Basic realm="Login Required"`.
  Every endpoint uses the `require_auth` decorator **except `/api/next-events`**.
- Content type: `application/json` (via `jsonify`) unless noted.
- Timestamps: strings `"YYYY-MM-DD HH:MM:SS"` in the **server's local time**,
  no timezone (written with SQLite `datetime('now','localtime')`). They are the
  time the backend received the message, not the device's measurement time.
- "online": `now - devices.last_seen < HEARTBEAT_TIMEOUT`. `HEARTBEAT_TIMEOUT`
  is defined twice in `server.py` (300, then 120). The later assignment wins,
  so the effective value is **120 s**.
- Default device: many endpoints take `?device=` and default to `"home_ping_esp32"`.
- Errors: there's no shared error format. Bad integer query params (`limit`, `minutes`)
  and missing JSON bodies raise exceptions, so Flask returns its default HTML `500`.
- CORS: none configured. The web page does not use the REST API.

## Clients

| Client | Endpoints used |
|---|---|
| Android `HomeFragment` (`MainActivity.kt`) | `/api/latest_all`, `/api/devices`, `/api/stats24h_combined`, `/api/restarts`, `/api/history_combined`, `/api/air`, `/api/hourly`, `POST /api/occupancy_arm`, `/api/occupancy_status` |
| Android `MainActivity` (FCM) | `POST /api/register_token` |
| Android `HomeEnvWidget` | `/api/latest` |
| Web (`web/index.html`) | none (MQTT only) |
| Firmware | none. **UNCERTAIN**: eink_node declares `API_URL` (template `.../api/latest`) + credentials but never calls it |

## Endpoint index

| Method | Path | Auth | Used by |
|---|---|---|---|
| GET | `/api/latest` | Basic | Android widget |
| GET | `/api/latest_all` | Basic | Android Home |
| GET | `/api/latest2` | Basic | none found |
| GET | `/api/history` | Basic | none found |
| GET | `/api/history_combined` | Basic | Android Home |
| GET | `/api/stats24h` | Basic | none found |
| GET | `/api/stats24h_combined` | Basic | Android Home |
| GET | `/api/hourly` | Basic | Android Home |
| GET | `/api/air` | Basic | Android Home |
| GET | `/api/devices` | Basic | Android Home |
| GET | `/api/restarts` | Basic | Android Home |
| GET | `/api/occupancy_rolling` | Basic | none found |
| GET | `/api/presence/latest` | Basic | none found |
| GET | `/api/presence/history` | Basic | none found |
| GET | `/api/widget` | Basic | none found (the actual widget uses `/api/latest`) |
| POST | `/api/register_token` | Basic | Android (FCM) |
| POST | `/api/occupancy_arm` | Basic | Android Home |
| GET | `/api/occupancy_status` | Basic | Android Home |
| GET | `/api/next-events` | **none** | none found (broken, see below) |

---

## Endpoints

### GET `/api/latest`

Latest env reading for `home_ping_esp32` plus combined radar occupancy.
Response: a **positional JSON array** (not an object), or `[]` if the device
has no `readings` rows.

| Index | Value | Type | Source |
|---|---|---|---|
| 0 | ts | string | `readings.ts` (latest row for `home_ping_esp32`) |
| 1 | temp | number/null | °C |
| 2 | humidity | number/null | %RH |
| 3 | uptime | integer | s |
| 4 | occupied | 0/1 | `get_combined_occupancy()`: 1 if any radar has ≥2 of its last 3 rows (within 2 min) occupied |
| 5 | confidence | integer 0–100 | `calculate_combined_confidence(15)`: highest per-radar mean of `occupied` over 15 min, ×100 |
| 6 | dark | 0/1 | `readings.dark` (always 0 today) |
| 7 | online | bool | `home_ping_esp32` heartbeat |

```json
["2026-10-13 19:42:10", 22.4, 48.3, 86400, 1, 73, 0, true]
```

Client: `HomeEnvWidget` reads indexes 1, 2, 4, 5, 7.

### GET `/api/latest_all`

Latest state of every device, keyed by device ID, plus a `combined` entry.

For each device with `readings` rows:

```json
{"ts": "...", "temp": 22.4, "humidity": 48.3, "uptime": 86400,
 "occupied": 0, "occupancy_confidence": 0, "dark": 0,
 "co2": null, "pressure": 1013.6, "online": true, "co2_ok": null}
```

`co2_ok` comes from in-memory state (`CO2_SENSOR_OK`): bool for devices that
have sent `co2_ok` since the backend started, otherwise `null`.

For each device with `presence_readings` rows, the radar fields are merged in.
If the device has no env rows, a stub with `temp`/`humidity`/`dark`/`co2`/`pressure` = null is used.
Then **`ts` and `uptime` are overwritten** with the radar row's values:

```json
{"ts": "...", "uptime": 86395, "occupied": 1, "occupancy_confidence": 73,
 "online": true, "presence": 1, "stationary": 1, "moving": 0,
 "distance": 142, "signal": 37, "rssi": -61, "radar_connected": 1}
```

Radar booleans are integers 0/1 here (as stored in SQLite).
`occupancy_confidence` = `calculate_confidence(device, 15)` (0–100).

Plus:

```json
"combined": {"occupied": 1, "confidence": 73}
```

Full example (abridged):

```json
{
  "home_ping_esp32": {"ts":"2026-10-13 19:42:15","temp":22.4,"humidity":48.3,"uptime":86405,"occupied":1,"occupancy_confidence":73,"dark":0,"co2":null,"pressure":1013.6,"online":true,"co2_ok":null,"presence":1,"stationary":1,"moving":0,"distance":142,"signal":37,"rssi":-61,"radar_connected":1},
  "hallway_presence_esp32": {"temp":null,"humidity":null,"dark":null,"co2":null,"pressure":null,"ts":"2026-10-13 19:42:13","uptime":3605,"occupied":0,"occupancy_confidence":0,"online":true,"presence":0,"stationary":0,"moving":0,"distance":0,"signal":0,"rssi":-70,"radar_connected":1},
  "co2_sensor_esp32": {"ts":"2026-10-13 19:41:58","temp":null,"humidity":null,"uptime":7260,"occupied":0,"occupancy_confidence":0,"dark":0,"co2":812,"pressure":null,"online":true,"co2_ok":true},
  "combined": {"occupied":1,"confidence":73}
}
```

Client: Android Home reads
`home_ping_esp32.{temp, humidity, uptime}`, `hallway_presence_esp32.uptime`,
`co2_sensor_esp32.{co2, uptime, co2_ok}`, `combined.{occupied, confidence}`.

### GET `/api/latest2?device=<id>`

Latest 2 `readings` rows for a device (default `home_ping_esp32`), newest first.
Response: array of positional arrays `[ts, temp, humidity, occupied, occupancy_confidence, dark]`.
`occupied`/`occupancy_confidence` from `readings` are always 0 now.
Client: none found.

### GET `/api/history?device=<id>`

Last 30 `readings` rows for a device (default `home_ping_esp32`), newest first.
Response: array of `[ts, temp, humidity, uptime, occupied, occupancy_confidence, dark]`.
Client: none found.

### GET `/api/history_combined`

Last 30 rows across **all** devices, merging `readings` and `presence_readings`,
ordered by `ts` DESC (string sort).
Response: array of positional arrays:

| Index | Value | Env rows | Radar rows |
|---|---|---|---|
| 0 | ts | string | string |
| 1 | device | string | string |
| 2 | temp | number/null | null |
| 3 | humidity | number/null | null |
| 4 | occupied | 0 (always) | 0/1 |
| 5 | occupancy_confidence | 0 (always) | `100` if `moving`, else raw `signal` (0–100) |

```json
[["2026-10-13 19:42:15","home_ping_esp32",null,null,1,37],
 ["2026-10-13 19:42:10","home_ping_esp32",22.4,48.3,0,0]]
```

Client: Android Home "readings" table. Radar rows arrive every 5 s from two
nodes, so the 30 rows usually cover only about 75 s and are almost all radar rows.

### GET `/api/stats24h?device=<id>`

24 h averages for one device's env data plus radar occupancy stats.

```json
{"avg_temp": 22.1, "avg_humidity": 47.9, "avg_occupancy": 41.0,
 "avg_confidence": 38, "occupied_hours": 9.84, "coverage_hours": 24.0}
```

- `avg_temp`/`avg_humidity`: mean over rows with both non-null, rounded to 1 dp. **`0` (not null) when there are no rows.**
- `avg_occupancy`: % (0–100, rounded to 0 dp) from `occupancy_stats(24)`. The highest-occupancy radar wins.
- `occupied_hours`: that radar's span × occupied fraction (2 dp).
- `coverage_hours`: longest per-radar span of data in the window (1 dp).
- `avg_confidence`: `calculate_combined_confidence(60)`, i.e. **last 60 minutes**, not 24 h.

Client: none found.

### GET `/api/stats24h_combined`

Same response shape as `/api/stats24h`, but `avg_temp`/`avg_humidity` average
over **all** devices' `readings` rows in the last 24 h. No `device` param.
Client: Android Home reads `avg_temp` (double), `avg_humidity` (int),
`avg_confidence` (int), `occupied_hours` (double).

### GET `/api/hourly?device=<id>`

Per-hour buckets **since local midnight today** (not a rolling 24 h), newest first.
Env averages come from `readings` for `device` (default `home_ping_esp32`).
Occupancy comes from `presence_readings` for `home_ping_esp32` only (`PRESENCE_DEVICE`).

```json
[{"hour":"2026-10-13 19:00","avg_temp":22.3,"avg_humidity":48.0,"avg_occupancy":62.0,"avg_confidence":62.0}]
```

- `avg_temp`/`avg_humidity`: number or null (1 dp).
- `avg_occupancy`: mean(`occupied`)×100, rounded to 0 dp. `0` if no radar rows that hour.
- `avg_confidence`: **identical to `avg_occupancy`**.

Client: Android Home occupancy bar strip (reads `hour`, `avg_occupancy`; reverses the order).

### GET `/api/air`

Latest CO2 and pressure from whichever device last reported them.

```json
{"ts":"2026-10-13 19:42:10","co2":812,"band":"fair","pressure":1013.6,
 "pressure_3h_change":-1.2,"temp":22.4,"humidity":48.3}
```

- `co2`: integer ppm or null. Latest non-null `readings.co2` from any device.
- `band`: `"good"` (<800), `"fair"` (<1200), `"stuffy"` (<2000), `"bad"` (≥2000), `"unknown"` (null).
- `pressure`/`temp`/`humidity`: from the latest row with non-null `pressure`.
- `pressure_3h_change`: hPa, latest pressure minus the newest reading at least 3 h old (1 dp), or null.
- `ts`: env row ts if present, else co2 row ts, else null.

Client: Android Home reads only `pressure` and `pressure_3h_change`. CO2 on Android comes from `/api/latest_all`.

### GET `/api/devices`

Every row in the `devices` table.

```json
[{"device":"home_ping_esp32","last_seen":"2026-10-13 19:42:15","online":true}]
```

Sensor nodes only. (Until #15, `/api/register_token` added an `"unknown"` row for
the phone; the backend deletes that row on start.)
Client: Android Home reads `device`, `online` for `home_ping_esp32`,
`hallway_presence_esp32`, `co2_sensor_esp32`.

### GET `/api/restarts`

Restart counts per device from `events` where `type='RESTART'`.

```json
{"co2_sensor_esp32":{"last_24h":1,"last_7d":3,"last_restart":"2026-10-13 07:12:40"}}
```

Devices with no restart ever are absent. Client: Android Home sums `last_24h`.

### GET `/api/occupancy_rolling?minutes=<n>`

`{"minutes": 60, "confidence": 38}`. Default `minutes=60`. `confidence` = `calculate_combined_confidence(minutes)`.
Client: none found.

### GET `/api/presence/latest`

Latest radar row for **`home_ping_esp32` only**, or `404 {"error":"No data"}`.

```json
{"ts":"...","occupied":1,"presence":1,"stationary":1,"moving":0,"distance":142,
 "signal":37,"rssi":-61,"uptime":86405,"radar_connected":1,
 "online":true,"occupancy_confidence":73,"occupancy_armed":false}
```

`occupancy_armed` (bool) is the same value `/api/occupancy_status` returns as `armed`.
Client: none found.

### GET `/api/presence/history?limit=<n>`

Latest `limit` (default 100) radar rows for **`home_ping_esp32` only**.
`[{"ts","occupied","presence","stationary","moving","distance","signal","rssi"}, ...]`.
Client: none found.

### GET `/api/widget?device=<id>`

```json
{"reading": ["2026-10-13 19:42:10", 22.4, 48.3, 1, 0],
 "events": [["2026-10-13 19:30:02","OCCUPANCY_CHANGE","occupied"]],
 "occupancy_armed": false}
```

- `reading`: `[ts, temp, humidity, combined_occupied, dark]` or null.
- `events`: last 5 `events` rows for `device` or `home_ping_esp32`, as `[ts, type, value]`.

Client: none found. The Android widget calls `/api/latest` instead.

### POST `/api/register_token`

Request (JSON):

```json
{"token": "<FCM registration token>", "device": "<optional label>"}
```

- `token` required. Missing → `400 {"error":"No token provided"}`.
- `device` optional and ignored. Phones aren't written to the `devices` table
  (that table is for sensor nodes).
- Subscribes the token to the FCM topic `env` (server side).
- Response: `{"success": true}`.
- Non-JSON or missing body → 500 (`request.json` is None).

Client: Android `MainActivity.registerTokenServerSide` sends `{"token": ...}` only.

### POST `/api/occupancy_arm`

Request: `{"armed": true|false}` (truthy/falsy, missing = false).
Sets `system_state.occupancy_armed` to `'1'`/`'0'` and logs event
`OCCUPANCY_ARMED` (device `"system"`). Response: `{"armed": true|false}`.
Client: Android Home toggle switch.

### GET `/api/occupancy_status`

Current arm state for occupancy-arrival pushes, read from `system_state.occupancy_armed`.
Response: `{"armed": true|false}` (same shape as the `POST /api/occupancy_arm` response).
`false` until the switch is first armed.
Client: Android Home, on startup, to set the toggle switch.

### GET `/api/next-events` (no auth, broken)

Intended to return the next `MAX_EVENTS` (3) calendar events, sorted by `start`.
It calls `list_calendars()` and `get_events()`, **which aren't defined anywhere
in `server.py`**, so every call returns:

```json
{"error":"internal_exception","message":"name 'list_calendars' is not defined"}
```

with status 500. The related helper `get_access_token()` references undefined
`TOKEN_FILE` and `SCOPES`. **UNCERTAIN**: a removed Google Calendar feature.
The endpoint has no `@require_auth`, so the exception message is visible to
anyone who can reach the server.

---

## Mismatches and issues between components

1. ~~**`/api/occupancy_status` doesn't exist.**~~ Fixed in #3: the backend now
   implements `GET /api/occupancy_status` → `{"armed": bool}`, which Android reads on startup.
2. **Android crashes if `home_ping_esp32.temp` is null.** In `/api/latest_all`
   the main node's `temp`/`humidity` can be null (BME280 missing or no env rows yet, radar
   stub). Android calls `getDouble("temp")`/`getInt("humidity")` inside
   `runOnUiThread`. A `JSONException` there isn't caught by the
   surrounding `try`, which is on the background thread (**UNCERTAIN**: crash vs. caught,
   needs a device test). The same applies to `co2Data.getInt("uptime")` if it were ever null.
3. **`ts`/`uptime` in `/api/latest_all` are silently overwritten** for devices that send both env
   and radar data (`home_ping_esp32`). The `ts` shown is the radar's, not the
   temperature reading's.
4. ~~Soil timestamp field name~~, 5. ~~soil threshold~~, 6. ~~soil endpoints without a
   data source~~: the soil feature (`/api/soil/*`, Android Plants tab) was removed in #9.
7. ~~**`register_token` pollutes `devices`.**~~ Fixed in #15: `register_token` no
   longer calls `touch_device`, and the old `"unknown"` row is deleted on start.
8. **Duplicate FCM topic subscription.** Android subscribes itself to topic `env`
   client-side *and* the backend subscribes the token server-side. This is harmless but redundant.
9. **Positional array responses** (`/api/latest`, `/api/latest2`, `/api/history`,
   `/api/history_combined`, `/api/widget.reading`, `/api/widget.events`) make
   clients depend on column order. The comment in `/api/widget` ("Same shape as before")
   suggests the order has already changed once.
10. **`occupied`/`occupancy_confidence` in `readings` are always 0** (the backend hard-codes
    them for env messages). They still appear in `/api/history`, `/api/latest2`
    and `/api/history_combined` env rows, where they look like real data.
11. **`dark` is always 0** (no firmware sends it) but is returned by `/api/latest`,
    `/api/latest_all`, `/api/widget`.
12. **`avg_confidence` doesn't mean the same thing everywhere.** In `/api/stats24h*` it covers the last 60 min,
    not 24 h. In `/api/hourly` it equals `avg_occupancy`. In
    `/api/history_combined` "confidence" for radar rows is `100`/`signal`, which is a different
    quantity from `calculate_confidence`.
13. **Hallway radar is invisible to several endpoints.** `/api/presence/*` and the
    occupancy column of `/api/hourly` only use `home_ping_esp32`.
14. **Empty-state values don't match.** `/api/stats24h*` return `0` instead of null
    for no data, `/api/latest` returns `[]`, and `/api/presence/latest` returns 404.
15. **Android config.** The server base URL and Basic-auth credentials come from the
    gitignored `android/local.properties` (`API_BASE_URL`, `API_USER`, `API_PASS`)
    via `BuildConfig`. `API_BASE_URL` has no trailing slash and falls back to
    `https://www.example.com` if unset. Note that `BuildConfig` values are compiled
    into the APK and can be extracted from it.
16. **Unused endpoints** (no client in this repo): `/api/latest2`, `/api/history`,
    `/api/stats24h`, `/api/occupancy_rolling`, `/api/presence/latest`,
    `/api/presence/history`, `/api/widget`, `/api/next-events`. **UNCERTAIN**: they may have
    external users not in this repo.
17. **`HEARTBEAT_TIMEOUT` defined twice** (300 with comment "seconds", later 120).
    The effective value is 120 s. Anyone reading the top of the file gets the wrong number.
