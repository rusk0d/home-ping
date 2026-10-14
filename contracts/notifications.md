# Push notifications contract (DRAFT)

Status: draft, reverse-engineered from `backend/server.py` and
`android/.../MyFirebaseMessagingService.kt`, `MainActivity.kt` on branch
`contracts-draft` (2026-10-13). Anything marked **UNCERTAIN** is inferred.

## Transport

- Backend: `firebase_admin.messaging.send()`, using the service account at
  `firebase_service_account.json` (relative to the backend's working directory).
- Target: **FCM topic `env`** for every push. No per-device tokens are targeted.
- Every message has both a `notification` block (`title`, `body`) and a `data` map.
  FCM requires all `data` values to be strings.
- Send failures are caught and printed (`[ERROR] Failed to send push`). There's no retry
  and the failure isn't recorded in the DB. The matching `events` row and cooldown
  are still recorded after a failed send.

### Subscription to topic `env`

- Android `MainActivity.onCreate` calls `FirebaseMessaging.subscribeToTopic("env")`.
- Android also sends its token to `POST /api/register_token`, and the backend calls
  `messaging.subscribe_to_topic([token], "env")`.
- `MyFirebaseMessagingService.onNewToken` only logs the token. It doesn't re-register.

### Android handling

- `onMessageReceived` reads only `notification.title` (fallback `"HomeEnv Alert"`)
  and `notification.body`, and posts a local notification on channel
  `homeenv_channel` ("HomeEnv Notifications", IMPORTANCE_HIGH). Tapping it opens `MainActivity`.
- The **`data` map is ignored** by the app.
- Every notification uses ID `0`, so a new alert replaces the previous one in the tray.
- **UNCERTAIN**: when the app is in the background, FCM shows `notification` messages
  in the system tray itself and `onMessageReceived` isn't called. Those alerts then
  use the default channel and icon, not `homeenv_channel`.

## Cooldowns and state

- `NOTIFICATION_COOLDOWN = 10 * 90` = **900 s (15 min)**, per alert key, stored in memory
  (`LAST_NOTIFICATION`). All of it resets on backend restart.
- Cooldown keys: `HUMIDITY_<device>` (shared by high and low), `TEMP_<device>`,
  `SOIL_DRY_<device>`, `RADAR_<device>`. The occupancy alert has **no cooldown key**.

## Notifications

All pushes go to topic `env`. For `data` keys, see each entry.

### 1. Humidity high: `HUMIDITY_HIGH`

- Trigger: an env MQTT message (no `presence`, no `moisture`+`raw`) with both
  `temp_c` and `humidity` present, `humidity > 65`, and no humidity push for this
  device in the last 900 s. Currently only `home_ping_esp32` via `home/esp32/bme280`.
- Notification: title `Humidity Alert`, body `Humidity is high: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_HIGH", "device": "<device>"}`
- Also logs event `HUMIDITY_HIGH_ALERT`.

```json
{"notification":{"title":"Humidity Alert","body":"Humidity is high: 67.2%"},
 "data":{"event":"HUMIDITY_HIGH","device":"home_ping_esp32"},"topic":"env"}
```

### 2. Humidity low: `HUMIDITY_LOW`

- Trigger: same as above but `humidity < 25`. Shares the cooldown with high.
- Notification: title `Humidity Alert`, body `Humidity is low: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_LOW", "device": "<device>"}`
- Logs event `HUMIDITY_LOW_ALERT`.

### 3. Temperature low: `TEMP`

- Trigger: env message with `temp_c` and `humidity` present, `temp_c < 18`, cooldown 900 s per device.
- Notification: title `Temperature Alert`, body `Temperature is low on {device}: {temp:.1f}°C`
- Data: `{"event": "TEMP", "device": "<device>"}`
- Logs event `TEMP_ALERT`.
- There's no high-temperature alert.

### 4. Radar disconnected: `RADAR_DISCONNECTED`

- Trigger: presence message where `RADAR_FAIL_COUNT[device] >= 12` (12 consecutive
  messages with `radar_connected=false`, ~60 s at 5 s intervals), and no radar push
  for this device in the last 900 s.
- The counter resets only when `radar_connected=true` arrives, so while the radar
  stays disconnected **the push repeats every 15 min**.
- Notification: title `Radar Disconnected`, body `{device}: LD2410C not responding`
- Data: `{"event": "RADAR_DISCONNECTED", "device": "<device>"}`
- Logs event `RADAR_DISCONNECTED`.
- Doesn't fire if the ESP32 itself goes offline (no messages, so the counter never moves). There's
  no device-offline push at all.

### 5. Occupancy arrival: `OCCUPIED`

- Trigger: presence message where all of these hold:
  - `system_state.occupancy_armed == '1'` (set by the Android switch via `POST /api/occupancy_arm`)
  - derived `occupied == 1` **and** `moving == 1`
  - `any_radar_quiet_minutes() >= 60` (`QUIET_MINUTES_BEFORE_ALERT`): no
    `occupied=1` row from any radar in the last 60 min (see `db-schema.md` for the quirk)
- No cooldown. It's naturally limited because the quiet window resets once occupancy is logged.
- Notification: title `Occupancy Alert`,
  body `Movement in the {where} after {N} min of quiet ({distance}cm)`. `where` is
  `hallway` if the device ID contains `"hallway"`, else `main room`.
- Data: `{"event": "OCCUPIED", "device": "<device>", "distance": "<int as string>"}`
- Logs event `OCCUPANCY_ALERT`.
- With no `occupied=1` row in the table at all, quiet = 9999, so the body reads "after 9999 min of quiet".

### 6. Soil dry: `SOIL_DRY`

- Trigger: soil MQTT message (`moisture` and `raw` present) with `moisture < 25`
  (`SOIL_DRY_THRESHOLD`), cooldown 900 s per device.
- Notification: title `Plant Needs Water`, body `{device}: Soil moisture is low ({moisture}%)`
- Data: `{"event": "SOIL_DRY", "device": "<device>", "moisture": "<value as string>"}`
- Logs event `SOIL_DRY_ALERT`.
- No soil firmware exists in this repo (see `mqtt.md`).

### Not a push

- `/api/next-events` and `get_access_token()` reference Google OAuth/calendar code that is
  incomplete. It's not related to FCM.
- `CLAUDE.md` lists `backend/test_push.py`, but **that file doesn't exist** in the repo.

## Summary table

| `data.event` | Title | Condition | Cooldown | Source |
|---|---|---|---|---|
| `HUMIDITY_HIGH` | Humidity Alert | humidity > 65 %RH | 15 min/device (shared with LOW) | env msg |
| `HUMIDITY_LOW` | Humidity Alert | humidity < 25 %RH | 15 min/device (shared with HIGH) | env msg |
| `TEMP` | Temperature Alert | temp < 18 °C | 15 min/device | env msg |
| `RADAR_DISCONNECTED` | Radar Disconnected | ≥12 consecutive `radar_connected=false` | 15 min/device, repeats | presence msg |
| `OCCUPIED` | Occupancy Alert | armed, moving, ≥60 min quiet | none | presence msg |
| `SOIL_DRY` | Plant Needs Water | moisture < 25 | 15 min/device | soil msg |

## Mismatches and issues

1. **The `data` payload is unused.** The backend sends `event`/`device`/etc., but the Android
   app only reads `notification.title`/`body`.
2. **Cooldowns reset on backend restart, and retained MQTT messages are replayed on
   reconnect.** A restart while humidity is above 65 or temp is below 18 sends the alert again
   immediately, even if one was sent minutes earlier.
3. **Failed sends still start the cooldown and log `*_ALERT` events** with value "sent",
   so the events table can claim a push went out when it didn't.
4. **The soil threshold differs from Android's.** The backend alerts below 25. The app's "Needs Water"
   label shows below 30.
5. **`moisture` missing in a soil message crashes the soil handler.** `moisture < 25` with None raises
   `TypeError`. It's caught in `on_message`, but only after the DB row was written.
6. **The `NOTIFICATION_COOLDOWN` expression is misleading.** `10 * 90` = 900 s matches the "15 minutes"
   comment, but reads like a typo for `15 * 60`. Worth confirming the intended value.
7. **The temperature/humidity alerts gate on both values.** A device that sends only `temp_c` (or only
   `humidity`) never alerts on either.
8. **There's no device-offline push**, even though heartbeat data exists. When a node drops off, Android only
   shows it if someone opens the app.
9. **Notification ID 0 is reused**, so multiple alerts collapse into one in the Android tray.
10. **Duplicate topic subscription.** The client-side and server-side subscriptions to `env` are redundant. If the backend
    `register_token` call fails, the client-side subscription still works.
