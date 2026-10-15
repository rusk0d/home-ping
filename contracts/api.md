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
  Every endpoint uses the `require_auth` decorator.
- Content type: `application/json` (via `jsonify`) unless noted.
- Timestamps: strings `"YYYY-MM-DD HH:MM:SS"` in the **server's local time**,
  no timezone (written with SQLite `datetime('now','localtime')`). They are the
  time the backend received the message, not the device's measurement time.
- "online": `now - devices.last_seen < HEARTBEAT_TIMEOUT` (**120 s**).
- Responses are JSON objects or arrays of objects; no positional arrays (since #14).
- No data: values are `null` (status 200), never `0`, `[]` or 404 (since #14).
- "confidence" (`confidence`, per radar or combined) always means: % of a radar's rows in the
  last **15 min** (`CONFIDENCE_MINUTES`) that were occupied, 0–100, or null without radar
  data. Combined = the highest radar. Other occupancy figures are called "occupancy".
- Default device: many endpoints take `?device=` and default to `"home_ping_esp32"`.
- Errors: JSON `{"error": "<message>"}` with the HTTP status (400 bad request body,
  404 unknown path, 405 wrong method, 500 unexpected error without details). The only
  exception is the 401 from Basic auth (text/plain, see Auth).
- CORS: none configured. The web page does not use the REST API.

## Clients

| Client | Endpoints used |
|---|---|
| Android `HomeFragment` | `/api/latest_all`, `/api/devices`, `/api/stats24h_combined`, `/api/restarts`, `/api/air`, `/api/hourly`, `POST /api/occupancy_arm`, `/api/occupancy_status` |
| Android `MainActivity` (FCM) | `POST /api/register_token` |
| Android `HomeEnvWidget` | `/api/latest` |
| Web (`web/index.html`) | none (MQTT only) |
| Firmware | none. **UNCERTAIN**: eink_node declares `API_URL` (template `.../api/latest`) + credentials but never calls it |

## Endpoint index

| Method | Path | Auth | Used by |
|---|---|---|---|
| GET | `/api/latest` | Basic | Android widget |
| GET | `/api/latest_all` | Basic | Android Home |
| GET | `/api/stats24h_combined` | Basic | Android Home |
| GET | `/api/hourly` | Basic | Android Home |
| GET | `/api/air` | Basic | Android Home |
| GET | `/api/devices` | Basic | Android Home |
| GET | `/api/restarts` | Basic | Android Home |
| POST | `/api/register_token` | Basic | Android (FCM) |
| POST | `/api/occupancy_arm` | Basic | Android Home |
| GET | `/api/occupancy_status` | Basic | Android Home |

---

## Endpoints

### GET `/api/latest`

Latest env reading for `home_ping_esp32` plus combined radar occupancy (the widget's data).
Always 200; fields are null when there is no data.

```json
{"ts":"2026-10-13 19:42:10","temp":22.4,"humidity":48.3,"uptime":86400,
 "online":true,"occupied":1,"confidence":73}
```

| Field | Type | Source |
|---|---|---|
| `ts` | string/null | latest `readings` row for `home_ping_esp32` |
| `temp` | number/null | °C |
| `humidity` | number/null | %RH |
| `uptime` | integer/null | s |
| `online` | bool | `home_ping_esp32` heartbeat |
| `occupied` | 0/1/null | `get_combined_occupancy()`: 1 if any radar has ≥2 of its last 3 rows (within 2 min) occupied, null if no radar reported in 2 min |
| `confidence` | integer 0–100/null | `calculate_combined_confidence()` (see "confidence" above) |

Client: `HomeEnvWidget` reads `temp`, `humidity`, `occupied`, `confidence`, `online`.

### GET `/api/latest_all`

Latest state of every device, keyed by device ID, plus a `combined` entry.

For each device with `readings` rows:

```json
{"ts": "...", "temp": 22.4, "humidity": 48.3, "uptime": 86400,
 "co2": null, "pressure": 1013.6, "online": true, "co2_ok": null}
```

`co2_ok` comes from the device's latest `readings` row (`readings.co2_ok`): bool if
that message had `co2_ok`, otherwise `null`. It survives backend restarts.

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
  "home_ping_esp32": {"ts":"2026-10-13 19:42:15","temp":22.4,"humidity":48.3,"uptime":86405,"co2":null,"pressure":1013.6,"online":true,"co2_ok":null,"occupied":1,"confidence":73,"presence":1,"stationary":1,"moving":0,"distance":142,"signal":37,"rssi":-61,"radar_connected":1},
  "hallway_presence_esp32": {"temp":null,"humidity":null,"co2":null,"pressure":null,"ts":"2026-10-13 19:42:13","uptime":3605,"occupied":0,"confidence":0,"online":true,"presence":0,"stationary":0,"moving":0,"distance":0,"signal":0,"rssi":-70,"radar_connected":1},
  "co2_sensor_esp32": {"ts":"2026-10-13 19:41:58","temp":null,"humidity":null,"uptime":7260,"co2":812,"pressure":null,"online":true,"co2_ok":true},
  "combined": {"occupied":1,"confidence":73}
}
```

Client: Android Home reads
`home_ping_esp32.{temp, humidity, uptime}`, `hallway_presence_esp32.uptime`,
`co2_sensor_esp32.{co2, uptime, co2_ok}`, `combined.{occupied, confidence}`.

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

### GET `/api/hourly?device=<id>`

Per-hour buckets **since local midnight today** (not a rolling 24 h), newest first.
Env averages come from `readings` for `device` (default `home_ping_esp32`).
Occupancy comes from `presence_readings` for `home_ping_esp32` only (`PRESENCE_DEVICE`).

```json
[{"hour":"2026-10-13 19:00","avg_temp":22.3,"avg_humidity":48.0,"avg_occupancy":62.0}]
```

- `avg_temp`/`avg_humidity`: number or null (1 dp).
- `avg_occupancy`: mean(`occupied`)×100, rounded to 0 dp. null if no radar rows that hour.

Client: Android Home occupancy bar strip and sparklines (reads `hour`, `avg_occupancy`,
`avg_temp`, `avg_humidity`; reverses the order). A null `avg_occupancy` is drawn as "no data".

### GET `/api/air`

Latest CO2 and pressure from whichever device last reported them.

```json
{"ts":"2026-10-13 19:42:10","co2":812,"band":"fair","pressure":1013.6,
 "pressure_3h_change":-1.2,"temp":22.4,"humidity":48.3}
```

- `co2`: integer ppm or null. Latest non-null `readings.co2` from any device.
- `band`: `"good"` (<800), `"fair"` (<1200), `"stuffy"` (<2000), `"bad"` (≥2000), null when there is no CO2 reading.
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
- Missing, non-JSON or non-object body → `400 {"error": "JSON object body required"}`.

Client: Android `MainActivity.registerTokenServerSide` sends `{"token": ...}` only.

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
9. ~~**Positional array responses**~~ Fixed in #14: `/api/latest` is an object,
   `/api/history_combined` was removed.
10. ~~**Always-0 `readings.occupied`/`occupancy_confidence`**~~ and
11. ~~**always-0 `dark`**~~: dropped from the responses and the `readings` table in #14.
12. ~~**`avg_confidence` doesn't mean the same thing everywhere.**~~ Fixed in #14: "confidence"
    is the 15-min definition everywhere; the 60-min and hourly duplicates were removed.
13. **Hallway radar is invisible to the occupancy column of `/api/hourly`**, which only
    uses `home_ping_esp32`.
14. ~~**Empty-state values don't match.**~~ Fixed in #14: null with status 200 everywhere.
15. **Android config.** The server base URL and Basic-auth credentials come from the
    gitignored `android/local.properties` (`API_BASE_URL`, `API_USER`, `API_PASS`)
    via `BuildConfig`. `API_BASE_URL` has no trailing slash and falls back to
    `https://www.example.com` if unset. Note that `BuildConfig` values are compiled
    into the APK and can be extracted from it.
16. ~~**Unused endpoints**~~ removed in #14: `/api/latest2`, `/api/history`,
    `/api/stats24h`, `/api/occupancy_rolling`, `/api/presence/latest`,
    `/api/presence/history`, `/api/widget`, `/api/next-events` (and the broken Google
    OAuth helper `get_access_token`).
17. ~~**`HEARTBEAT_TIMEOUT` defined twice**~~ Fixed in #14: one definition, 120 s.
