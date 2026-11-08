# Deep review — 2026-11-08

Scope: the whole codebase on `main` at `12b41d3` (#381). This morning's review
(`2026-11-08-full-review.md`, at `2472592`) read every backend module but only the changed
or alert-related parts of the app, the firmware and the web page. This one reads in full what
that review skimmed:
- every Kotlin file under `android/app/src/main`, including `ui/*`;
- `main_node`, `eink_node`, `fridge_node` and the HomePing library;
- `web/app.js`;
- `backend/tools/*`.

It also re-reads everything merged since `2472592`: 34 files, #350–#381. Read-only: nothing
was changed.

Each finding below was checked against the code by hand. Line numbers are the current ones.

## 0. State

- **Backend tests:** 67/67 pass, and `ruff check` is clean.
- **Server:** `/opt/iot` matches `main` (`deploy.sh --check`).
- **Firmware:** all three sketches have been flashed with the current library ("the new WiFi
  connect thing"), and #368 (85 °C skip) is on the fridge node.
- **Open issues:**
  - #229: baseline tuning, parked until December.
  - #334: outdoor probe.
- **This morning's review:** every finding is fixed and merged:
  - H1 in #364, M1 in #365/#366, M2 in #367/#368;
  - L1/L2 in #369, L3 in #370, L4 in #371, L5 in #372, L6 in #373, L7 in #374/#375, L8 in #379;
  - the door card in #376/#377.
- **Overall:** no High findings. The app, the firmware and the tools had not been read in full
  before, and they hold up well. What's left is one robustness issue in the shared WiFi code,
  plus small things.

## 1. Findings

### Medium

| ID | Finding | Fix |
|---|---|---|
| M1 | **A WiFi outage of more than 30 s reboots every node.** `hpConnectWiFi()` (`HomePing.h:17`) blocks while it waits and calls `ESP.restart()` after 60 × 500 ms (`:31`). All three sketches call it from `loop()` as soon as `WiFi.status() != WL_CONNECTED` (`main_node.ino:243`, `eink_node.ino:408`, `fridge_node.ino:152`). A router restart or a firmware update on the access point takes 1–3 min. That means 2–5 reboots per node, each logged as a RESTART. Above 2 in 24 h, each node turns the Devices dot red (`MAX_RESTARTS_24H`). On the **fridge node**, every reboot resets the condition timers (they're in RAM). The backend keeps the alert state, so there is no false all-clear (#300). But a fridge that starts warming during an outage needs the full 30 min / 2 h again after the last reboot. Radar and MQTT also stop for the 30 s, because `loop()` is blocked. | Reboot only at boot. In `loop()`, rely on the ESP32's own auto-reconnect (`WiFi.setAutoReconnect(true)`, the default) and only check, without blocking. Restart only after a long outage, e.g. 10 min without WiFi, via a `millis()` timestamp. A small `hpWiFiWatchdog()` in the library would replace the three `loop()` calls. The host test (`test/test_homeping.cpp`) can cover its timing with the fakes. |

### Low

| ID | Finding | Fix |
|---|---|---|
| L1 | **The e-ink loop can stall 5 s a minute without NTP.** `render()` calls `getLocalTime(&timeinfo)` (`eink_node.ino:449`), whose default timeout is 5000 ms. As long as time hasn't synced (NTP blocked, or a DNS problem after boot), every redraw blocks `loop()` for 5 s. During that time no radar frames are read and nothing is published. | `getLocalTime(&timeinfo, 0)`: the header already copes with `hasTime == false`. |
| L2 | **"today"/"yesterday" is wrong at the end of a leap year.** `HomeFormat.whenText` (`HomeFormat.kt:79`) counts days as `year difference × 365 + day-of-year difference`. On 1 Jan 2029, 31 Dec 2028 (day 366) counts as 0 days ago and shows as "today 23:00". 30 Dec counts as "yesterday". It only matters in the first days of January after a leap year. | Count days with `java.time`: `ChronoUnit.DAYS.between(thenLocalDate, nowLocalDate)` (minSdk 26 has it). |
| L3 | **Two small things on the notification settings screen.** `NotificationSettingsFragment` launches its loads and saves in the fragment's `lifecycleScope` (`:74`, `:122`, `:136`, `:153`), not `viewLifecycleOwner.lifecycleScope` as the other fragments do (#125). The code after the `withContext` touches the binding `b`. Today the fragment and its view live and die together in the pager, so this can't crash, but a later change could make it. Also, `pickTime` formats `"%02d:%02d"` with the default locale (`:111`). With Arabic or Persian digits the backend's `HH:MM` check would reject the time ("Couldn't save"). | `viewLifecycleOwner.lifecycleScope` and `String.format(Locale.US, …)`, like the rest of the app. |
| L4 | **`validate_db_deep.py` flags normal behaviour and misses the fridge.** Check 11 (`:409`) counts two `*_ALERT` events of the same type and device within `NOTIFICATION_COOLDOWN` as a violation. Since #251 a failed push is retried after 60 s and logged again ("failed, …" then "sent, …"). Muted alerts are logged too (#321). So one network hiccup during an alert makes the tool report a cooldown violation. Check 10 (`:348`, min ≤ avg ≤ max in `hourly`) doesn't check the fridge's `fridge_min/avg/max` (#331). | Check 11: only count rows whose value starts with `sent`. Check 10: add the fridge columns. |

## 2. Checked and fine

These were read in full and hold up:

- **App networking:**
  - Every `Api` call runs on `Dispatchers.IO` and returns null or `Fetched(error)`, never throws.
  - `HomeFragment` and `NotificationHistoryFragment` use the view's lifecycle scope.
  - The history tab ignores a load whose "show muted" switch changed meanwhile.
- **Widget:**
  - Fetches run under `goAsync()` with an 8 s call timeout, inside the ~10 s budget.
  - `/api/air` runs in parallel, so both fit in that budget.
  - The alarm doesn't wake the phone.
  - `MY_PACKAGE_REPLACED` restarts the alarm chain.
- **Push taps (#354):**
  - One request code per notification slot.
  - Home sits under the target screen.
  - Home is `CLEAR_TOP | SINGLE_TOP`, so no second copy is opened.
- **Ignoring Devices issues (#380):**
  - An ignore is forgotten only after a *complete* load, so a failed request doesn't start the 24 h.
  - An issue coming back clears its "gone since".
  - Versions change on a new restart, `last_seen`, a drill run or a cert change.
  - WiFi has no version on purpose (it comes and goes).
- **Geofence auto-arm:**
  - The arm goes through a unique `WorkManager` job (REPLACE, network constraint, backoff); a 4xx isn't retried.
  - A manual flip cancels a pending location arm and sets the override until the next crossing.
  - The geofence is re-added on boot, after an update and when Home opens.
- **Charts and gauges:** every `min == max` case is padded, so there is no division by zero. Lines break at hours without data, and lone points are drawn. Every number is formatted with `Locale.US`.
- **Firmware:**
  - MQTT connect is non-blocking (5 s between attempts), with a 512-byte buffer for the presence payload.
  - TLS has two roots (ISRG X1 and Root YR).
  - The CO₂ warm-up uses the 64-bit uptime.
  - `calibrate` doesn't block.
  - The BME280 is re-initialised after 3 failed reads.
  - On the e-ink node, the BKK fetch runs on core 0 under a mutex, and the JSON is streamed through a filter.
  - The fridge node's conditions have hysteresis, and the 85 °C value is skipped.
- **Web page:**
  - No `innerHTML` (CSP-safe).
  - Retained messages don't count as a node being live.
  - The `offline` last will wins until newer live data arrives.
  - Bad login (CONNACK 4/5) stops retrying.
  - Reconnects back off 5 s → 30 s.
  - The fridge needle uses the app's −2…10 °C scale.
- **Tools:** read-only connections (`mode=ro`), they refuse to run as root, and the API login comes from `.env`. `fridge_door_replay.py` drives the same `fridge.door_step` as the backend.
- **Today's backend changes (#350–#376):**
  - `ignore_quiet` affects only the quiet-hours check; the switch still mutes.
  - The urgent flag is set only after a *sent* urgent push. It survives a milder step, so the recovery that replaces a "too warm" also comes through at night.
  - The backup `.tmp`, then `os.replace`, cleans up after itself on failure.
  - The drill's age check runs after the restore and validation, so an old backup is still proven restorable.
  - The fridge range guard runs before storage.

## 3. Feature ideas

- **Dim an ignored issue on its device card too:** the Restarts / Last reset rows stay red
  after the issue is ignored at the top (#380). Muted, not red, while ignored.
- **Fridge on the widget** (from this morning's list): still open. A fourth value, or just the
  alert colour.
- **Firmware host test in CI:** `libraries/HomePing/test` has fakes and a test file, but the
  workflow only compiles the sketches. Running the host test there would cover M1's watchdog.

## 4. Suggested order

1. **M1:** firmware, one library change and three one-line sketch changes. It needs an OTA flash
   of all three nodes. Do it together with **L1** (e-ink).
2. **L2, L3:** the app, in one PR.
3. **L4:** tools, small.
