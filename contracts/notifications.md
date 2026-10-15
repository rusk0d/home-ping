# Push notifications contract (DRAFT)

Status: draft, reverse-engineered from `backend/server.py` and
`android/.../MyFirebaseMessagingService.kt`, `MainActivity.kt` on branch
`contracts-draft` (2026-10-13). Anything marked **UNCERTAIN** is inferred.

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

### Subscription to topic `env`

- Android `MainActivity.onCreate` calls `FirebaseMessaging.subscribeToTopic("env")`.
- Android also sends its token to `POST /api/register_token`, and the backend calls
  `messaging.subscribe_to_topic([token], "env")`.
- `MyFirebaseMessagingService.onNewToken` re-registers a rotated token with
  `POST /api/register_token` (`PushRegistration`, shared with `MainActivity`).

### Android handling

- `onMessageReceived` reads `title`/`body`/`event`/`device` from `data`, falling back to
  a `notification` block (older backends). Tapping the notification opens `MainActivity`.
- Channels (created at app start, IMPORTANCE_HIGH): `occupancy` ("Occupancy alerts") for
  `OCCUPIED`, `homeenv_channel` ("Sensor alerts") for everything else. The manifest makes
  `homeenv_channel` FCM's default channel for notification messages it shows itself.
- Notification ID = hash of `event/device`: a repeat of the same alert replaces the
  previous one, different alerts stack.

## Cooldowns and state

- `NOTIFICATION_COOLDOWN = 15 * 60` = **900 s (15 min)**, per alert key, stored in memory
  (`LAST_NOTIFICATION`). All of it resets on backend restart. Starts only after a
  successful send (failed sends retry after 60 s).
- Cooldown keys: `HUMIDITY_<device>` (shared by high and low), `TEMP_<device>`,
  `RADAR_<device>`. The occupancy alert has **no cooldown key**.

## Notifications

All pushes go to topic `env`. For `data` keys, see each entry.

### 1. Humidity high: `HUMIDITY_HIGH`

- Trigger: an env MQTT message (no `presence`) with `humidity`
  present (`temp_c` not needed), `humidity > 65`, and no humidity push for this
  device in the last 900 s. Currently only `home_ping_esp32` via `home/esp32/bme280`.
- Notification: title `Humidity Alert`, body `Humidity is high: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_HIGH", "device": "<device>"}` (plus `title`, `body`)
- Also logs event `HUMIDITY_HIGH_ALERT`.

```json
{"data":{"title":"Humidity Alert","body":"Humidity is high: 67.2%",
         "event":"HUMIDITY_HIGH","device":"home_ping_esp32"},
 "android":{"priority":"high"},"topic":"env"}
```

### 2. Humidity low: `HUMIDITY_LOW`

- Trigger: same as above but `humidity < 25`. Shares the cooldown with high.
- Notification: title `Humidity Alert`, body `Humidity is low: {hum:.1f}%`
- Data: `{"event": "HUMIDITY_LOW", "device": "<device>"}`
- Logs event `HUMIDITY_LOW_ALERT`.

### 3. Temperature low: `TEMP`

- Trigger: env message with `temp_c` present (`humidity` not needed), `temp_c < 18`, cooldown 900 s per device.
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

(`SOIL_DRY`, "Plant Needs Water", was removed with the soil feature in #9.)

## Summary table

| `data.event` | Title | Condition | Cooldown | Source |
|---|---|---|---|---|
| `HUMIDITY_HIGH` | Humidity Alert | humidity > 65 %RH | 15 min/device (shared with LOW) | env msg |
| `HUMIDITY_LOW` | Humidity Alert | humidity < 25 %RH | 15 min/device (shared with HIGH) | env msg |
| `TEMP` | Temperature Alert | temp < 18 °C | 15 min/device | env msg |
| `RADAR_DISCONNECTED` | Radar Disconnected | ≥12 consecutive `radar_connected=false` | once per outage | presence msg |
| `OCCUPIED` | Occupancy Alert | armed, moving, ≥60 min quiet | none | presence msg |

## Mismatches and issues

1. ~~**The `data` payload is unused.**~~ Fixed in #12: data-only messages, the app reads `data`.
2. **Cooldowns reset on backend restart, and retained MQTT messages are replayed on
   reconnect.** A restart while humidity is above 65 or temp is below 18 sends the alert again
   immediately, even if one was sent minutes earlier.
3. ~~**Failed sends still start the cooldown and log "sent".**~~ Fixed in #12: logged as
   `failed, ...`, retried after 60 s.
4. ~~Soil threshold differs~~, 5. ~~missing `moisture` crashes the soil handler~~: soil feature removed in #9.
6. ~~**The `NOTIFICATION_COOLDOWN` expression is misleading.**~~ Fixed in #12: `15 * 60`.
7. ~~**The temperature/humidity alerts gate on both values.**~~ Fixed in #12: each needs only its own value.
8. **There's no device-offline push**, even though heartbeat data exists. When a node drops off, Android only
   shows it if someone opens the app.
9. ~~**Notification ID 0 is reused.**~~ Fixed in #12: one ID per `event/device`.
10. **Duplicate topic subscription.** The client-side and server-side subscriptions to `env` are redundant. If the backend
    `register_token` call fails, the client-side subscription still works.
