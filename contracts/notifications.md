# Push notifications contract

Push notifications from the backend (`backend/alerts.py`, `nodes.py`, `ingest.py`) to the
Android app (`MyFirebaseMessagingService.kt`, `MainActivity.kt`) via Firebase Cloud Messaging.
It is the source of truth: update it in the same change as the code (see CLAUDE.md).
First written on 2026-10-13 by reading the code; anything still marked **UNCERTAIN** was
inferred then and hasn't been confirmed since.

## Transport

- Backend: `firebase_admin.messaging.send()`, using the service account at
  `firebase_service_account.json` (relative to the backend's working directory).
- Target: **FCM topic `env`** for every push. No per-device tokens are targeted.
- **Data-only messages** (since #12): no `notification` block. `title` and `body` travel
  in the `data` map next to `event`, `device`, etc., so the Android app always builds the
  notification itself (also in the background). FCM requires all `data` values to be strings.
- `android.priority = "high"`, so data messages are delivered while the phone is in Doze.
- `send_push()` returns whether FCM accepted the message. `alert()` logs the matching
  `events` row as `sent, ...` or `failed, ...`. The cooldown only starts after a
  successful send; a failed send is retried after `PUSH_RETRY_SECONDS` (60 s).
- A muted alert (see "Settings") is logged as `muted (off), ...` or
  `muted (quiet hours), ...` and sends nothing (since #82).

### Subscription to topic `env`

- Android `MainActivity.onCreate` calls `Firebase.messaging.register()` (FID-based
  registration) and `subscribeToTopic("env")`, on every start (cheap when already done).
  FID registration is opt-in: manifest meta-data `firebase_messaging_installation_id_enabled`.
- The app sends nothing to the backend (since #153): registration tokens are deprecated,
  and the backend only sends to topic `env`. `MyFirebaseMessagingService.onRegistered`
  only logs.
- App versions from before #153 still send their token to `POST /api/register_token`
  (legacy), and the backend calls `messaging.subscribe_to_topic([token], "env")`.

### Android handling

- `onMessageReceived` reads `title`/`body`/`event`/`device` from `data`, falling back to
  a `notification` block (older backends). Tapping the notification opens `MainActivity`.
- Channels (created at app start, IMPORTANCE_HIGH): `occupancy` ("Occupancy alerts") for
  `OCCUPIED`, `homeenv_channel` ("Sensor alerts") for everything else. The manifest makes
  `homeenv_channel` FCM's default channel for notification messages it shows itself.
- Notification ID = hash of `event/device`: a repeat of the same alert replaces the
  previous one, different alerts stack.

## Settings (since #82)

Set in the app (`GET`/`POST /api/notification_settings`, see `api.md`), stored in `system_state`.

- One switch per alert type (`alert(kind=...)`): `humidity` (HUMIDITY_HIGH/LOW),
  `temperature` (TEMP), `radar` (RADAR_DISCONNECTED), `nodes` (NODE_STATUS, offline
  and back online). All on by default.
- **Quiet hours**: off by default. When on, the alert types above are muted between
  `start` and `end` (server local time, Budapest; the window may cross midnight,
  `start` inclusive, `end` exclusive).
- **Occupancy alerts (`OCCUPIED`) are never muted**: they keep their own Armed switch
  and come through during quiet hours.
- A muted alert counts as handled: its cooldown starts (so it's logged at most once per
  15 min, like a sent one) and once-per-outage alerts (radar, node offline) are not
  repeated after quiet hours end or the switch is turned back on.
- Node back online: pushed only if that outage's offline push was actually sent.
  If the offline alert was muted there is nothing to replace on the phone, so the
  back-online push is skipped (logged as `NODE_ONLINE` `no push (offline alert was muted), ...`).

## Cooldowns and state

- `NOTIFICATION_COOLDOWN = 15 * 60` = **900 s (15 min)**, per alert key, in `LAST_NOTIFICATION`
  and persisted as `system_state` `cooldown:<key>` (#5), so a backend restart doesn't reset it.
  Starts only after a successful send (failed sends retry after 60 s).
- The radar "outage already pushed" flags (`RADAR_ALERTED`) are persisted the same way
  (`radar_alerted:<device>`).
- Cooldown keys: `HUMIDITY_<device>` (shared by high and low), `TEMP_<device>`,
  `RADAR_<device>`. The occupancy alert has **no cooldown key**.
- Humidity and temperature alert **once per excursion** (#117): the push (or a muted alert)
  sets `system_state` `env_alerted:HUMIDITY_<device>` (`high`/`low`) or `env_alerted:TEMP_<device>`
  (`low`). While it is set, no further push for that condition. It is cleared when the value is
  back in range by the hysteresis margin (`HUMIDITY_HYSTERESIS` 2 %RH, `TEMP_HYSTERESIS` 0.5 °C):
  humidity < 63 after high, > 27 after low, temperature > 18.5. The 15-min cooldown still applies
  on top, against flapping around the threshold. A failed send sets nothing (retried after 60 s).

## Notifications

All pushes go to topic `env`. For `data` keys, see each entry.

### 1. Humidity high: `HUMIDITY_HIGH`

- Trigger: an env MQTT message (no `presence`) with `humidity`
  present (`temp_c` not needed), `humidity > 65`, not already alerted for this excursion
  (`env_alerted:HUMIDITY_<device>` ≠ `high`, see "Cooldowns and state"), and no humidity push for
  this device in the last 900 s. Currently only `home_ping_esp32` via `home/esp32/bme280`.
- Notification: title `Humidity Alert`, body `Humidity is high: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_HIGH", "device": "<device>"}` (plus `title`, `body`)
- Also logs event `HUMIDITY_HIGH_ALERT`.

```json
{"data":{"title":"Humidity Alert","body":"Humidity is high: 67.2%",
         "event":"HUMIDITY_HIGH","device":"home_ping_esp32"},
 "android":{"priority":"high"},"topic":"env"}
```

### 2. Humidity low: `HUMIDITY_LOW`

- Trigger: same as above but `humidity < 25` (excursion flag `low`). Shares the cooldown with high.
- Notification: title `Humidity Alert`, body `Humidity is low: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_LOW", "device": "<device>"}`
- Logs event `HUMIDITY_LOW_ALERT`.

### 3. Temperature low: `TEMP`

- Trigger: env message with `temp_c` present (`humidity` not needed), `temp_c < 18`, not already
  alerted for this excursion (`env_alerted:TEMP_<device>`, cleared above 18.5 °C), cooldown 900 s per device.
- Notification: title `Temperature Alert`, body `Temperature is low on {device}: {temp:.1f}°C`
- Data: `{"event": "TEMP", "device": "<device>"}`
- Logs event `TEMP_ALERT`.
- There's no high-temperature alert.

### 4. Radar disconnected: `RADAR_DISCONNECTED`

- Trigger: presence message where `RADAR_FAIL_COUNT[device] >= 12` (12 consecutive
  messages with `radar_connected=false`, ~60 s at 5 s intervals), and no radar push
  for this device in the last 900 s.
- **Once per outage**: after a successful push the device is in `RADAR_ALERTED` until
  `radar_connected=true` arrives, so a long outage doesn't repeat the push.
- Notification: title `Radar Disconnected`, body `{device}: LD2410C not responding`
- Data: `{"event": "RADAR_DISCONNECTED", "device": "<device>"}`
- Logs event `RADAR_DISCONNECTED`.
- Doesn't fire if the ESP32 itself goes offline (no messages, so the counter never moves). There's
  no device-offline push at all.

### 5. Occupancy arrival: `OCCUPIED`

- Trigger, in two steps:
  1. An arrival starts with the first derived `occupied == 1` presence message (moving or
     stationary) after `any_radar_quiet_minutes() >= 30` (`QUIET_MINUTES_BEFORE_ALERT`; 60
     until 2026-10-17): no `occupied=1` row from any radar in the last 30 min. The start time and quiet minutes are
     kept in `system_state` (`arrival_started`, `arrival_quiet`).
  2. The first presence message from any radar with `occupied == 1` **and** `moving == 1`
     within `ARRIVAL_CONFIRM_SECONDS` (**120 s**) of that start confirms it, and the push is sent
     if `system_state.occupancy_armed == '1'` (set by the Android switch via
     `POST /api/occupancy_arm`). A start without movement in time (e.g. a stationary ghost
     reading) expires without a push.
- No cooldown. Once per arrival: confirming clears `arrival_started`, and the quiet window
  resets once occupancy is logged.
- Before this, movement was required on the very first reading after the quiet spell, so an
  arrival first seen as a stationary target never alerted.
- Notification: title `Occupancy Alert`,
  body `Movement in the {where} after {N} min of quiet ({distance}cm)`. `where` is
  `hallway` if the device ID contains `"hallway"`, else `main room`.
- Data: `{"event": "OCCUPIED", "device": "<device>", "distance": "<int as string>"}`
- Logs event `OCCUPANCY_ALERT`.
- With no `occupied=1` row in the table at all, quiet = 9999, so the body reads "after 9999 min of quiet".
- `N` is the quiet spell that ended when the arrival started, not the time since the moving reading.

### 6. Node offline / back online: `NODE_STATUS` (since #13)

- Trigger: a check every 30 s (`node_check_loop`). A device in `devices` whose
  `last_seen` is older than `NODE_OFFLINE_ALERT_SECONDS` (**300 s**) is offline.
  That's longer than the API's 120 s "online" threshold, so a reboot or a short WiFi
  drop doesn't alert. Decided on data age, not on the MQTT last will (see `mqtt.md`).
- **Once per outage:** after a successful (or muted, #82) push the device is in `NODE_OFFLINE`
  (persisted as `system_state` `node_offline:<device>`: `'1'` pushed, `'m'` muted) until it sends data again.
  Cooldown key `NODE_<device>` (15 min, also limits flapping; failed sends retry after 60 s).
- No checks during the first 300 s after a backend start (every `last_seen` is old then).
- Offline: title `Node offline`, body `{device}: no data for {N} min`,
  data `{"event": "NODE_STATUS", "status": "offline", "device": "<device>"}`. Logs `NODE_OFFLINE`.
- Back online (the next message from that device): title `Node back online`,
  body `{device} is sending data again`, data `{"event": "NODE_STATUS", "status": "online", ...}`.
  Logs `NODE_ONLINE`. Same `event`/`device` as the offline push, so in the app it
  **replaces** the offline notification (one slot per `event/device`).
- A retired node (e.g. `co2_sensor_esp32` after the node merge) gets one offline push,
  then drops out of `devices` after 30 days.

(`SOIL_DRY`, "Plant Needs Water", was removed with the soil feature in #9.)

## Summary table

| `data.event` | Title | Condition | Cooldown | Source |
|---|---|---|---|---|
| `HUMIDITY_HIGH` | Humidity Alert | humidity > 65 %RH | once per excursion (until < 63), 15 min/device shared with LOW | env msg |
| `HUMIDITY_LOW` | Humidity Alert | humidity < 25 %RH | once per excursion (until > 27), 15 min/device shared with HIGH | env msg |
| `TEMP` | Temperature Alert | temp < 18 °C | once per excursion (until > 18.5), 15 min/device | env msg |
| `RADAR_DISCONNECTED` | Radar Disconnected | ≥12 consecutive `radar_connected=false` | once per outage | presence msg |
| `OCCUPIED` | Occupancy Alert | armed; first occupied reading after ≥30 min quiet, confirmed by movement within 120 s (#101) | once per arrival | presence msg |
| `NODE_STATUS` | Node offline / Node back online | no data for 5 min / data again | once per outage | 30 s check |

## Mismatches and issues

1. **Legacy server-side subscription.** `POST /api/register_token` is only called by app
   versions from before #153; remove it once none is installed.

## Changelog

Fixed mismatches, oldest first (issue number: what changed).

- #5: cooldowns persist across backend restarts; retained MQTT messages are skipped.
- #9: soil alerts removed (threshold mismatch, crash on a missing `moisture`).
- #12: data-only messages, the app reads `data`.
- #12: failed sends are logged as `failed, ...` and retried after 60 s instead of starting the cooldown.
- #12: `NOTIFICATION_COOLDOWN` written as `15 * 60`.
- #12: temperature and humidity alerts each need only their own value.
- #12: one notification ID per `event/device` instead of always 0.
- #13: device-offline push (`NODE_STATUS`).
- #153: the app registers with FCM by FID (`register()`) and no longer sends a token; `/api/register_token` is legacy.
