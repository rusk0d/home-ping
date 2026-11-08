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
  successful send; a failed send is retried after `PUSH_RETRY_SECONDS`: 1, 2, 5, 10 min for
  the 1st..4th failure in a row of that key, then every 15 min; a success starts over (#251).
- A muted alert (see "Settings") is logged as `muted (off), ...` or
  `muted (quiet hours), ...` and sends nothing (since #82).

### Subscription to topic `env`

- Android `MainActivity.onCreate` calls `Firebase.messaging.register()` (FID-based
  registration) and `subscribeToTopic("env")`, on every start (cheap when already done).
  FID registration is opt-in: manifest meta-data `firebase_messaging_installation_id_enabled`.
- The app sends nothing to the backend (since #153): registration tokens are deprecated,
  and the backend only sends to topic `env`. `MyFirebaseMessagingService.onRegistered`
  only logs.

### Android handling

- `onMessageReceived` reads `title`/`body`/`event`/`device` from `data`, falling back to
  a `notification` block (older backends). Tapping the notification opens `MainActivity`.
- Channels (created at app start, IMPORTANCE_HIGH): `occupancy` ("Occupancy alerts") for
  `OCCUPIED`, `air` ("Air quality", IMPORTANCE_LOW: no sound or vibration, #245) for `AIR`,
  `homeenv_channel` ("Sensor alerts") for everything else. The manifest makes
  `homeenv_channel` FCM's default channel for notification messages it shows itself.
- Notification ID = hash of `event/device`: a repeat of the same alert replaces the
  previous one, different alerts stack.

## Settings (since #82)

Set in the app (`GET`/`POST /api/notification_settings`, see `api.md`), stored in `system_state`.

- One switch per alert type (`alert(kind=...)`): `humidity` (HUMIDITY_HIGH/LOW),
  `temperature` (TEMP), `radar` (RADAR_DISCONNECTED), `nodes` (NODE_STATUS, offline
  and back online; also the server watchdog, WATCHDOG, #176, and silent sensors, SENSOR_STATUS,
  #224), `pressure` (PRESSURE, #237), `air` (AIR, #245), `baseline` (BASELINE, #229), `fridge` (FRIDGE_ALERT, #290; FRIDGE_DOOR, #336).
  All on by default.
- **Quiet hours**: off by default. When on, the alert types above are muted between
  `start` and `end` (server local time, Budapest; the window may cross midnight,
  `start` inclusive, `end` exclusive).
- **Occupancy alerts (`OCCUPIED`) are never muted**: they keep their own Armed switch
  and come through during quiet hours.
- A muted alert counts as handled: its cooldown starts (so it's logged at most once per
  15 min, like a sent one) and once-per-outage alerts (radar, node offline) are not
  repeated after quiet hours end or the switch is turned back on. Some alerts are
  **postponed** by quiet hours instead of muted (node offline since #350, the server
  watchdog, silent sensors, air): held back and pushed at the first check after quiet hours
  if the condition still holds.
- Node back online: pushed only if that outage's offline push was actually sent.
  If the offline alert was muted there is nothing to replace on the phone, so the
  back-online push is skipped (logged as `NODE_ONLINE` `no push (alert was muted), data again`).

## Cooldowns and state

- `NOTIFICATION_COOLDOWN = 15 * 60` = **900 s (15 min)**, per alert key, in `LAST_NOTIFICATION`
  and persisted as `system_state` `cooldown:<key>` (#5), so a backend restart doesn't reset it.
  Starts only after a successful send (failed sends retry after 1, 2, 5, 10, then every 15 min).
- The radar "outage already pushed" flags (`RADAR_ALERTED`) are persisted the same way
  (`radar_alerted:<device>`).
- **Excursion state** (#305): humidity, temperature, pressure, node offline, server watchdog,
  sensor silent and baseline share one helper (`alerts.Excursion`) and one `system_state` value
  format: `''` while there's none, else how its start went, `sent` or `muted`, with `:<band>`
  where the condition has bands (`sent:high`, `muted:falling`). It's saved only once the push
  went out, so a failed one is retried at the next check. Another band while one is on is a new
  excursion. The end push (where there is one) only follows a `sent` start; after a `muted` one
  the end is logged as `no push (alert was muted), <detail>`. The fridge (`fridge_alert_*`) and
  air (`air:*`) keys keep their own format.
- Cooldown keys: `HUMIDITY_<device>` (shared by high and low), `TEMP_<device>`,
  `RADAR_<device>`. The occupancy alert has **no cooldown key**.
- Humidity and temperature alert **once per excursion** (#117): the push (or a muted alert)
  sets `system_state` `env_alerted:HUMIDITY_<device>` (band `high`/`low`) or `env_alerted:TEMP_<device>`
  (band `low`), see "Excursion state". While it is set, no further push for that condition. It is cleared when the value is
  back in range by the hysteresis margin (`HUMIDITY_HYSTERESIS` 2 %RH, `TEMP_HYSTERESIS` 0.5 °C):
  humidity < 63 after high, > 27 after low, temperature > 18.5. The 15-min cooldown still applies
  on top, against flapping around the threshold. A failed send sets nothing (retried with the back-off above).

## Notifications

All pushes go to topic `env`. For `data` keys, see each entry.

### 1. Humidity high: `HUMIDITY_HIGH`

- Trigger: an env MQTT message (no `presence`) with `humidity`
  present (`temp_c` not needed), `humidity > 65`, not already alerted for this excursion
  (`env_alerted:HUMIDITY_<device>` ≠ `sent:high` / `muted:high`, see "Cooldowns and state"), and no humidity push for
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

- Trigger: env message from a room device (not `NON_ROOM_DEVICES`, i.e. not the fridge, #290) with
  `temp_c` present (`humidity` not needed), `temp_c < 18`, not already
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
  (persisted as `system_state` `node_offline:<device>`: `sent` / `muted`) until it sends data again.
- Switch `nodes` (off mutes it). **Quiet hours postpone it** (since #350; muted before, so a node
  that died at night was never reported): no push and not in `NODE_OFFLINE` while they last, pushed
  at the first 30 s check after them if the node is still offline. A node back before then gets no push.
  Cooldown key `NODE_<device>` (15 min, also limits flapping; failed sends retry with the back-off above).
- No checks during the first 300 s after a backend start (every `last_seen` is old then).
- Offline: title `Node offline`, body `{device}: no data for {N} min`,
  data `{"event": "NODE_STATUS", "status": "offline", "device": "<device>"}`. Logs `NODE_OFFLINE`.
- Back online (the next message from that device): title `Node back online`,
  body `{device} is sending data again`, data `{"event": "NODE_STATUS", "status": "online", ...}`.
  Logs `NODE_ONLINE`. Same `event`/`device` as the offline push, so in the app it
  **replaces** the offline notification (one slot per `event/device`).
- A retired node (e.g. `co2_sensor_esp32` after the node merge) gets one offline push,
  then drops out of `devices` after 30 days.

### 7. Server watchdog: `WATCHDOG` (since #176)

- Trigger: `check_watchdogs()` in the summaries loop, every 5 min (`server_checks.py`).
- `device` = the check: `backup` when the newest `backups/data-*.db.gz` is older than
  `BACKUP_MAX_AGE_HOURS` (**26 h**; no `backups` folder = no check), `disk` when less than
  `DISK_MIN_FREE_MB` (**1024 MB**) is free on the database's disk, `offsite` (since #122)
  when `backups/.offsite-ok` (touched by `backup_db.py` after each upload to S3) is older than
  `OFFSITE_MAX_AGE_HOURS` (**26 h**) or missing; no `BACKUP_S3_BUCKET` in `.env` = no check;
  `drill` (since #339) when the last monthly restore drill (`backups/.drill.json`, written by
  `backup_drill.py`) failed, or is older than `DRILL_MAX_AGE_DAYS` (**35**) or missing; no
  `DRILL_AWS_ACCESS_KEY_ID` in `.env` = no check.
- **Once per excursion**, persisted as `system_state` `watchdog:<check>` (`sent`,
  `muted` by the `nodes` switch, `''` fine). A failed send is retried at the next check.
- Switch `nodes`. **Quiet hours postpone it** to the first check after they end, instead
  of muting it (the backup runs at 03:30); turning the switch off mutes it like other alerts.
- Push: title `Backup failed`, body `No database backup for over 26 h (last: …)`, or title
  `Disk almost full`, body `Only {N} MB free on the server`, or title `Off-site backup failed`,
  body `No backup uploaded to S3 for over 26 h (last: …)`, or title `Restore drill failed`, body
  `The S3 backup didn't restore cleanly: {reason}`, or title `Restore drill overdue`, body
  `No restore drill for over 35 days (last: …)`;
  data `{"event": "WATCHDOG", "device": "backup" | "disk" | "offsite" | "drill" | "cert_<port>"}`. Logs `WATCHDOG_BACKUP` /
  `WATCHDOG_DISK` / `WATCHDOG_OFFSITE` / `WATCHDOG_DRILL` (device `server`).
- Over (a backup newer than 26 h; free space ≥ 1024 + 256 MB; an upload newer than 26 h):
  title `Backup OK again` / `Disk space OK again` / `Off-site backup OK again` (body `The database
  backup reached S3 again`), same `event`/`device`, so it **replaces** the warning in the app.
  Only sent if the warning was. Logs `WATCHDOG_BACKUP_OK` / `WATCHDOG_DISK_OK` / `WATCHDOG_OFFSITE_OK`.
  The drill's: a drill that passed within 35 days → `Restore drill OK again`, body
  `{object} restored and validated`, `WATCHDOG_DRILL_OK`.
- Certificates (since #206): every `host:port` in `.env` `CERT_CHECK` (none = no check), at
  most once an hour (`CERT_CHECK_INTERVAL`). The backend connects like a client (trusted chain,
  host name) and reads the certificate's expiry. `device` = `cert_<port>` (one host per port).
  - Expires in less than `CERT_MIN_DAYS` (**14**; certbot renews at 30): title
    `Certificate expires soon`, body `{host:port} expires in {N} days ({date}); is the renewal running?`.
  - Fails verification (expired, wrong name, untrusted): title `Certificate problem`, body
    `{host:port}: {reason}`.
  - Unreachable (connection error): no push, logged as a warning; the node and uptime
    monitoring cover outages.
  - Over (valid for at least 14 days again): title `Certificate renewed`, body
    `{host:port} is valid until {date}`. Logs `WATCHDOG_CERT_<PORT>` / `WATCHDOG_CERT_<PORT>_OK`.

### 8. Sensor silent / back: `SENSOR_STATUS` (since #224)

- Trigger: `check_sensors()` every 30 s, in `node_check_loop` after the offline check
  (`nodes.py`). For each device online (`last_seen` within 300 s, not in `NODE_OFFLINE`) and
  each sensor it has in the raw `readings` (kept 2 days): `env` (BME280: a row with `temp`)
  and `co2` (MH-Z19C: a row with `co2` or `co2_ok`). A radar-only node has neither.
- **Silent:** no valid value for `SENSOR_SILENT_SECONDS` (**900 s**; `env`: `temp` not null,
  `co2`: `co2` not null and `co2_ok` = 1), while the node was sending other data at the start
  of that window (a row in the 5 min before it). So a node outage (that's `NODE_STATUS`), a
  backend outage, or a reboot (CO₂ warm-up ~3 min) doesn't alert. Covers a BME280 that
  publishes nothing and an MH-Z19C that keeps sending `co2_ok: false`.
- **Once per excursion**, persisted as `system_state` `sensor_silent:<device>:<sensor>`
  (`sent`, `muted` by the `nodes` switch, `''` fine). A failed send is retried at
  the next check. Switch `nodes`; **quiet hours postpone it**, like the watchdog.
- Push: title `Sensor not reporting`, body `{device}: no {BME280 | CO₂ sensor} readings for 15 min`,
  data `{"event": "SENSOR_STATUS", "status": "silent", "device": "<device>:<env|co2>"}`.
  Logs `SENSOR_SILENT_ENV` / `SENSOR_SILENT_CO2` (device = the node).
- Back (a valid value within the window again): title `Sensor reporting again`, body
  `{device}: {sensor} readings are back`, same `event`/`device` with `"status": "ok"`, so it
  **replaces** the warning in the app (one slot per sensor). Only pushed if the warning was;
  logs `SENSOR_OK_ENV` / `SENSOR_OK_CO2`.

### 9. Pressure falling fast: `PRESSURE` (since #237)

- Trigger: `check_pressure()` in the summaries loop, every 5 min right after the hourly rollup
  (`ingest.py`). Change = `pressure_avg` of the newest complete hour in `hourly` minus the hour
  `PRESSURE_WINDOW_HOURS` (**3**) before it. No check when either hour has no pressure or the
  newest is more than 2 h old (node down, fresh start).
- Push at a change ≤ `PRESSURE_DROP_THRESHOLD` (**−3.0 hPa**), a storm sign. A rise doesn't push.
- **Once per excursion** (#117 pattern): `system_state` `env_alerted:PRESSURE` = `sent:falling` /
  `muted:falling`; cleared once the change is back above threshold + `PRESSURE_HYSTERESIS`
  (**−2.0 hPa**). Cooldown key `PRESSURE` (15 min).
- Switch `pressure`; **quiet hours mute it** like the other environment alerts (not postponed).
- Push: title `Pressure falling fast`, body `{change:+.1f} hPa in 3 h: storm likely, bring things in from outside`,
  data `{"event": "PRESSURE", "device": "pressure"}`. Logs `PRESSURE_FALLING` (device `pressure`).
- Thresholds are conventional barometer rules (maritime climates); review after a winter of data.

### 10. Air: `AIR` (since #245 step 2)

`air.py`, every 60 s (`air_loop`), from the CO₂ node's last 10 min of readings
(`AIR_DEVICE`) and Open-Meteo's outside weather. Logs-only 2026-10-19..24, then pushes.
Switch `air`; **quiet hours postpone** the pushes (they go out at the next check after
quiet hours end, if still due), except "Window still open?", which they mute.
Android: its own silent channel (no sound or vibration), from the app change of #245.

| Push | `data.device` | When | Title / body |
|---|---|---|---|
| Time to air out | `co2` | CO₂ ≥ `AIR_CO2_HIGH` (**1200** ppm) for 10 min starts an excursion; pushed while the newest CO₂ is still ≥ 1200 and someone is home (a radar occupied within `QUIET_MINUTES_BEFORE_ALERT`, 30 min) | `Time to air out` / `CO₂ 1250 ppm: <advice>.`; later `CO₂ still 1300 ppm after 3 h: <advice>.` |
| reminder | `co2` | the same, `AIR_REMINDER_HOURS` (**3 h**) after the last push (sent or muted) | same, replaces the previous |
| Air is fresh again | `co2` | the excursion ends (CO₂ < `AIR_CO2_OK`, **900** ppm), only if a push of it was sent | `Air is fresh again` / `CO₂ is down to 850 ppm.` (replaces it) |
| Close the window? | `window` | once per window spell: open ≥ **20** min, CO₂ < **600** ppm, outside ≥ **5 °C** colder than inside | `Close the window?` / `Open for 25 min, CO₂ is down to 520 ppm and it's 6 °C outside: …` |
| Window still open? | `window` | `POST /api/occupancy_arm` with `armed: true, source: "location"` (geofence: left home) while a spell runs | `Window still open?` / `You left home and a window has been open for 12 min.` |

- `<advice>`: `open a window for 5-10 minutes`; `it's raining, tilt a window for a while`
  (precipitation > 0); `the outside air is humid, keep it short` (outside absolute humidity
  ≥ inside). The weather changes the advice, it never holds the push back.
- Nobody home or quiet hours at the start of an excursion: logged as `AIR_HIGH`
  `no push yet (<why>), …`, pushed once that's over if CO₂ is still ≥ 1200.
- A failed "Time to air out" is retried after `AIR_RETRY_MINUTES` (15); a failed
  "Close the window?" isn't (it's a hint).
- data `{"event": "AIR", "device": "co2" | "window"}`: one notification slot each.
- Window spells (`WINDOW_OPEN`/`WINDOW_CLOSED`) and the state keys: `db-schema.md`.

### 11. Fridge: `FRIDGE_ALERT` (since #290, fixed in #294 and #300)

The fridge node decides the alert state itself (`alert` in `home/esp32/fridge_temp`,
`mqtt.md`); `fridge.py` pushes when the newest reading's state differs from the one last
handled. Checked first in the summaries loop, every 5 min, as its own step: a failing
watchdog or rollup doesn't skip it (#303).

| `alert` | Title / body |
|---|---|
| `cold` | `Fridge too cold` / `Below 0 °C for 30+ minutes` |
| `warm_low` | `Fridge warming up` / `Above 5 °C for 30+ minutes` |
| `warm_critical` | `Fridge too warm` / `Above 8 °C for 30+ minutes` |
| `warm_critical_duration` | `Fridge too warm (sustained)` / `Above 5 °C for 2+ hours` |
| `""` after one of the above | `Fridge back to normal` / `Temperature is within normal range` |

- data `{"event": "FRIDGE_ALERT", "device": "fridge_temp_esp32", "alert": "<state>"}`, `"alert": ""`
  for the recovery: one notification slot, so the recovery replaces the alert (#300; before:
  `FRIDGE_NORMAL`). Logs `FRIDGE_ALERT` / `FRIDGE_ALERT_RECOVERY`.
- `system_state`: `fridge_alert_state` (the state last handled) and `fridge_alert_shown`
  (`sent` / `muted` / `''`). Both are saved only after the push went out, so a failed push is
  retried with the #251 backoff instead of being lost.
- **Muted** (switch `fridge` off, or quiet hours): saved as `muted`. If the state still holds once
  it isn't muted any more, it's pushed then; if it ends first, nothing is pushed, also no
  "back to normal" (#300).
- **Cooling down** from `warm_critical*` to `warm_low`/`cold`: saved without a push, the more
  severe notification stays until "back to normal".
- **Node reboots**: the node keeps its timers in RAM, so after a reboot it reports `""` (or a
  milder state) for up to 2 h. A step down while the node's `uptime` < 2 h and the reading is
  outside 0.5–4.5 °C is held as a reboot, not a recovery (#300). Without `uptime` (older
  firmware) a step down is taken as it comes.
- Cooldown per state: key `fridge:<state>` (`fridge:normal` for the recovery), 15 min. A state
  that comes back within its cooldown waits (pushed on the first check after it, if still on);
  a more severe one, e.g. `warm_low` -> `warm_critical`, pushes at once.
- The fridge isn't a room sensor: the room `TEMP` alert skips it (`NON_ROOM_DEVICES`).

### 11b. Fridge door left open: `FRIDGE_DOOR` (since #336)

The backend's own rule, from the readings (the node's `alert` doesn't know about the door):
`fridge.check_door()` on every fridge reading (ingest), so the push isn't up to 5 min late;
`fridge.door_step()` is the rule itself.

- **Rise**: the newest reading at least `FRIDGE_DOOR_RISE_C` (2.0 °C) above the lowest of the
  last `FRIDGE_DOOR_RISE_MINUTES` (5). The newest of those lowest readings is the base and the
  start. A compressor cycle warms far slower than that.
- **Open**: still above base + `FRIDGE_DOOR_CLOSED_C` (1.0 °C) `FRIDGE_DOOR_OPEN_MINUTES` (10)
  after the start → `Fridge door open?` / `Up from 3.0 to 7.5 °C in 10 min and not cooling down`.
  Once per rise (`alerts.Excursion`).
- **Closed**: back within `FRIDGE_DOOR_CLOSED_C` of the base → the rise is over; after a sent
  push `Fridge door closed` / `Back to 3.6 °C` replaces it. A door shut within a few minutes
  cools back before 10 min: nothing is pushed.
- data `{"event": "FRIDGE_DOOR", "device": "fridge_temp_esp32"}`: its own notification slot (not
  the temperature alert's). Logs `FRIDGE_DOOR_OPEN` / `FRIDGE_DOOR_CLOSED`.
- Switch `fridge`, quiet hours mute it (Excursion default): a muted open is saved as `muted` and
  its close only logged.
- `system_state`: `fridge_door_rise` (the watched rise) and `fridge_door` (the push's state).
- Tuning: `tools/fridge_door_replay.py` replays the stored raw readings (2 days) through
  `door_step()` and prints the rises and the pushes it would have sent; options override the limits.

### 12. Unusual reading: `BASELINE` (since #229, reworked in #297 and #302)

`baseline.py`, in the summaries loop every 5 min (its own step, after the rollup, #303), but each newly completed hour of `hourly` is
checked once (`baseline_last_hour`; an hour more than 3 h old isn't, after downtime).

- Metrics: temperature, humidity, CO₂ (`temp_avg`, `humidity_avg`, `co2_avg`; room devices only,
  #290). Not pressure: no daily rhythm, and fronts push `PRESSURE` already.
- Against the **same local hour of the day** (Budapest time: a DST change doesn't move the
  household's routine, #302) over the 28 days before: robust z-score, median and MAD,
  the MAD at least 1.5 °C / 5 %RH / 100 ppm. Needs 14 values, so a fresh install stays quiet for
  two weeks.
- `|z| > 3` for **2 consecutive hours** (a skipped hour, node down or no value, starts the count
  over, #302): `Unusual reading` / `Temperature is unusually high for this
  time of day: 30.0 °C (usually 22.0)`. Once under `|z| 2` (hysteresis): `Back to normal` /
  `Temperature is back to normal: 23.0 °C`, only if the alert was sent (a muted one ends quietly).
- data `{"event": "BASELINE", "device": "temp" | "humidity" | "co2"}`: one slot per metric, the
  recovery replaces the alert. Logs `BASELINE_DEVIATION` / `BASELINE_NORMAL` (device `system`).
- No cooldown: each metric is checked once an hour. The excursion state is saved only after the
  push went out, so a failed one is retried on the next hour. Switch `baseline`; quiet hours mute it.
- Before #297 it never fired: SQLite has no `strftime('%A')`, and 28 days give only 4-5 values
  per weekday-hour.

(`SOIL_DRY`, "Plant Needs Water", was removed with the soil feature in #9.)

## Outside the backend: uptime monitor (since #250)

Everything above is sent by the backend, so none of it can say that the server itself is
down or unreachable: on 2026-10-19 the VPS lost its network for 80 min (22:13-23:33, on the
host's side) and the only trace was failed `NODE_OFFLINE` pushes in the server's own log.

An outside check covers that: **UptimeRobot** (free plan, the user's account), alerts by its
own app/email, not by FCM.

| Monitor | Request | Every | Down when |
|---|---|---|---|
| web page | `HEAD https://<site>/` | 5 min | no answer, TLS error, non-2xx/3xx (nginx, certificate) |
| backend | `HEAD https://<site>/api/health` | 5 min | no answer, or 502 (backend not running) / 503 (`/api/health` not ok: MQTT down, DB error, no node message for 5 min, #77) |

- `/api/health` needs no login and its status code carries the result, so a plain HTTP
  monitor is enough (no keyword check on the body).
- Requests show in the nginx log as `UptimeRobot/2.0`; they come from changing IPs, so
  nothing in ufw/fail2ban is specific to them (they get 200s, nothing to ban).
- A backend restart shows as one failed check (502) when it lands on the check; that's
  expected, not an outage.

## Summary table

| `data.event` | Title | Condition | Cooldown | Source |
|---|---|---|---|---|
| `HUMIDITY_HIGH` | Humidity Alert | humidity > 65 %RH | once per excursion (until < 63), 15 min/device shared with LOW | env msg |
| `HUMIDITY_LOW` | Humidity Alert | humidity < 25 %RH | once per excursion (until > 27), 15 min/device shared with HIGH | env msg |
| `TEMP` | Temperature Alert | temp < 18 °C | once per excursion (until > 18.5), 15 min/device | env msg |
| `RADAR_DISCONNECTED` | Radar Disconnected | ≥12 consecutive `radar_connected=false` | once per outage | presence msg |
| `OCCUPIED` | Occupancy Alert | armed; first occupied reading after ≥30 min quiet, confirmed by movement within 120 s (#101) | once per arrival | presence msg |
| `NODE_STATUS` | Node offline / Node back online | no data for 5 min / data again | once per outage | 30 s check |
| `WATCHDOG` | Backup failed / Disk almost full / Off-site backup failed / Certificate expires soon, Certificate problem (+ "OK again", "Certificate renewed") | newest backup > 26 h old / < 1 GB free / no S3 upload for 26 h / certificate < 14 days or invalid | once per excursion | 5 min check (certificates hourly) |
| `SENSOR_STATUS` | Sensor not reporting / Sensor reporting again | node online, no valid BME280 / CO₂ value for 15 min / valid again | once per excursion | 30 s check |
| `PRESSURE` | Pressure falling fast | hourly pressure average −3.0 hPa or more below 3 h earlier | once per excursion (until > −2.0), 15 min | 5 min check after the hourly rollup |
| `AIR` | Time to air out / Air is fresh again (`co2`); Close the window? / Window still open? (`window`) | CO₂ ≥ 1200 ppm for 10 min, someone home / < 900 again; spell ≥ 20 min with fresh air and cold outside; geofence left home during a spell | every 3 h while high; once per spell; once per leave | 60 s check; `POST /api/occupancy_arm` |
| `FRIDGE_ALERT` | Fridge too cold / warming up / too warm / too warm (sustained) / back to normal | the node's `alert` state changed (<0 °C, >5 °C, >8 °C for 30 min; >5 °C for 2 h); a muted one is pushed once unmuted if still on | once per state change, 15 min per state | 5 min check |
| `FRIDGE_DOOR` | Fridge door open? / Fridge door closed | a rise of 2 °C within 5 min, not back within 1 °C of the start 10 min later / back within 1 °C | once per rise | each fridge reading |
| `BASELINE` | Unusual reading / Back to normal | hourly average \|z\| > 3 vs. the same local hour over 28 days, 2 consecutive hours / \|z\| < 2 again | once per excursion | each new hour (5 min check) |

## Mismatches and issues

None open.

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
- #176: watchdog pushes for an overdue backup and low disk space.
- #153: the app registers with FCM by FID (`register()`) and no longer sends a token; `/api/register_token` is legacy.
- #172: `POST /api/register_token` removed.
- #224: push when a sensor on a live node goes silent (`SENSOR_STATUS`).
- #237: pressure-falling push (`PRESSURE`), switch `pressure`.
- #245: air pushes (`AIR`), switch `air`, after five days logs-only.
- #250: outside uptime monitor (UptimeRobot on `/` and `/api/health`), documented here; no code change.
- #122: watchdog push when the nightly backup wasn't uploaded to S3 for 26 h (`offsite`).
- #251: failed pushes back off (1, 2, 5, 10, then every 15 min per key) instead of retrying every 60 s.
- #305: one excursion helper and state format (`sent`/`muted`[`:<band>`]); a muted watchdog's end is logged too.
- #336: fridge door left open push (`FRIDGE_DOOR`), switch `fridge`.
- #339: watchdog `drill`: the monthly restore drill from S3 failed or is overdue.
