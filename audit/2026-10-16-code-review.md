# Code review, 2026-10-16

Whole codebase at `main` = `1181a87` (after #116). Read-only: nothing was changed.
Follows up on `2026-10-15-code-review.md`. Everything from that review is fixed or tracked
(#59–#62, #66, #70, #72, #73, #75–#80), so this one only lists what is new or still open.

**Tracked as issues:** B1 → #117 · B2 → #118 · B3 → #119 · B4 → #120 · B5 → comment on #57 ·
S3 + lint → #121 · S5 → #122 · S4 + 5.1/5.2 → #123 · 5.3 → #124 · 5.4 + lint + deps → #125 ·
5.6 → #126 · 5.5 → #127 · ideas 4 → #128 · 5 → #129 · 6 → #130 · 3 (summary) → #131 · 8 → #132 ·
11 → #133 · 7 → comment on #80 · 10 → #79. S1/S2 were already #31/#60.

Severity: **High** = data loss or outage likely, **Medium** = real risk or user-visible bug,
**Low** = hygiene / small bug.

## 1. Git sync

- `main` = `origin/main` = `1181a87`. No open PRs, no stashes, working tree clean.
- Leftover local branch `feat/99-web-login`: fully merged (no commits outside `main`), safe to
  delete (`git branch -d feat/99-web-login`).

## 2. Security

### S1 (Medium, tracked): MQTT
Unchanged since the last review and tracked in #31 (node merge): the backend and all nodes share
the `esp32` account, the nodes talk plain MQTT on 1883 (credentials in clear text on the LAN), and
port 1883 is reachable from outside. Nothing new to add; listed so it isn't forgotten.

### S2 (Medium, tracked): API password in the APK
Tracked in #60. `BuildConfig.API_PASS` is in every APK; the app is a debug build.

### S3 (Low): CI hardening
- `.github/workflows/ci.yml` has no `permissions:` block, so the job token gets the repo's default
  (possibly write). `permissions: contents: read` at the top is enough for this workflow.
- Actions are pinned to major tags (`actions/checkout@v4`, …), not commit SHAs; the arduino
  libraries are installed unpinned (newest version on every run).

### S4 (Low): no request-size limit
Flask's `MAX_CONTENT_LENGTH` is unset; waitress accepts bodies up to 1 GB by default. The JSON
endpoints need a few hundred bytes. Requests are authenticated first, so the risk is small;
`app.config["MAX_CONTENT_LENGTH"] = 16 * 1024` closes it.

### S5 (Low): backups live next to the data
`/opt/iot/backups` is on the same disk as `data.db`. A lost VPS loses both. The README describes a
manual `scp` pull; nothing does it automatically.

Checked and fine: all SQL with an f-string only interpolates constants (`store.py` schema code);
user input always goes through parameters. The web page builds DOM with `textContent` only (no
`innerHTML`), so MQTT payloads can't inject markup. The API login is constant-time and fail2ban
covers it (#61). The backend runs as `iot` behind nginx on 127.0.0.1 (#59). `.env` and the
service-account key are mode 600.

## 3. Bugs and behaviour

### B1 (Medium): environment alerts repeat every 15 minutes while the condition lasts
`ingest.on_message`: the humidity and temperature alerts only have a 15-min cooldown, no
"once per excursion" state. Humidity at 66 % for an evening means a push every 15 minutes; a cold
room (< 18 °C) the same all night unless quiet hours are on. The radar and node alerts already
work once per outage with a flag (`radar_alerted`, `node_offline`).
**Fix:** a per-device "alerted" flag in `system_state`, set on the push and cleared when the value
is back inside the band with some hysteresis (e.g. humidity < 63 %, temperature > 18.5 °C).

### B2 (Low): e-ink node shows departed trams and doesn't sort
`eink_node.ino` `fetchAllDepartures()`:
- `BKK_URL` asks for `minutesBefore=2`, and a negative difference is clamped to 0 (`if (diff < 0) diff = 0`),
  so a tram that left up to 2 minutes ago is shown as `0'`. Skipping `diff < 0` (or
  `minutesBefore=0`) fixes it.
- The comment says "sort by time" but nothing sorts: the order is the API's (scheduled time), so a
  delayed tram can be listed above one that leaves earlier. A small insertion sort on `minutes`
  over the ≤10 entries fixes it.
- A failed BKK request and "no trams" both show "No departures"; showing "BKK unavailable" on
  failure would tell them apart.

### B3 (Low): co2_node reports "warming up" for 3 min every ~49.7 days
`co2_node.ino`: `bool warming = millis() < WARMUP_MS;`. When `millis()` wraps, the node claims to be
warming up and drops its readings for 3 minutes. Use `hpUptimeSeconds() < 180` (64-bit, #75).

### B4 (Low): a broker outage freezes the nodes
`hpConnectMQTT()` (firmware/libraries/HomePing) loops until the broker accepts. While Mosquitto is
down, `loop()` never gets past it: the radar UART isn't read (buffer overflows), and the e-ink
node stops refreshing the tram display, although BKK and WiFi work. **Fix:** one connect attempt
per call, with the 5 s backoff kept in a timestamp, so the loop goes on in between.

### B5 (Low, merge-related): the retired CO₂ node will raise "Node offline"
After the node merge, `co2_sensor_esp32` stops reporting; the offline check pushes once and the
device stays in `devices` (and the app's list) for 30 days (`DEVICES_RETENTION_DAYS`). Worth a step
in #57: `DELETE FROM devices WHERE device='co2_sensor_esp32'` and the `node_offline:` key, and drop it
from `NODES` in `web/index.html`.

## 4. Dead code and leftovers

- **Backend:** none. An AST scan finds no unused top-level function or constant.
- **Firmware:** `#include <WiFi.h>` / `<PubSubClient.h>` in the sketches are now also pulled in by
  `HomePing.h` (harmless). `StaticJsonDocument` / `DynamicJsonDocument` are deprecated in
  ArduinoJson 7, which CI installs (`JsonDocument` replaces both; a 7.x document grows as needed).
- **Android:** see section 7 (lint).
- **Repo:** branch `feat/99-web-login` (section 1). `audit/2026-10-15-code-review.md` is fully
  addressed; it can stay as history.

## 5. Refactoring candidates

1. **Backend: small helpers for repeated code.**
   - "Online" (`(utcnow() - strptime(last_seen)).total_seconds() < HEARTBEAT_TIMEOUT`) is written out
     three times in `api.py` (`latest`, `latest_all`, `devices`).
   - Parsing a stored timestamp (`datetime.strptime(x, "%Y-%m-%d %H:%M:%S")`) appears 8 times in 4
     modules. A `store.parse_ts()` helper covers both.
   - `"home_ping_esp32"` is hard-coded twice in `api.py` although `config.PRESENCE_DEVICE` exists.
2. **Backend: `stats24h_combined` averages in Python.** It loads every temp/humidity row of the last
   24 h and averages them in a list; `AVG()` in SQL does the same without the transfer.
3. **Tests: real pytest tests.** Since #73 importing the backend does nothing, so the 21 case
   scripts no longer need a process each. A `conftest.py` fixture (stub Firebase/paho, temp dir,
   `server.prepare()`) and plain `assert`s would give normal pytest output, `-k` selection and a
   shared setup instead of 21 copies of the stub preamble.
4. **Android: coroutines instead of raw threads.** 8 `thread { … runOnUiThread { if (isDestroyed) … } }`
   blocks across 4 files; `lifecycleScope.launch` + `withContext(Dispatchers.IO)` cancels with the
   screen and removes the `isDestroyed` checks. Logging uses `println`/`printStackTrace` (15×);
   `Log.w(TAG, …)` would show up properly in logcat.
5. **One source for the level bands.** CO₂ 800/1200/2000 (and the humidity/temperature bands) are
   defined in the backend (`/api/air` `band`), the web page and Android `Levels.kt`. The Android app
   could use the `band` the API already returns for CO₂; for the others a short note in
   `contracts/api.md` would at least name the one place to change them together.
6. **Firmware: ArduinoJson 7 API** (section 4) and a `JsonDocument` filter for the BKK response,
   which today is parsed into a 16 KB document although only a few fields are used.

## 6. Improvements and features

**Reliability / operations**
1. **Off-site backups** (S5): a nightly `rclone`/`scp` push of `backups/` to cloud storage or the
   home PC, or a weekly e-mail of the newest file.
2. **Dependency updates:** Dependabot for pip, Gradle and GitHub Actions (one config file). Android
   is behind: `firebase-messaging-ktx 23.2.0` (the `-ktx` artifacts are discontinued; use
   `firebase-messaging` with the Firebase BoM), `okhttp 4.11.0`, `core-ktx 1.12.0`, `material 1.11.0`.
3. **Alert hysteresis** (B1), and a **daily summary push** instead of repeated warnings (e.g. 21:00:
   "CO₂ above 1200 ppm for 2 h today, coldest 17.8 °C").

**App**
4. **One dashboard request.** The home screen makes 6 parallel requests every refresh
   (`latest_all`, `devices`, `stats24h_combined`, `restarts`, `air`, `hourly`). A single
   `/api/dashboard` would cut the requests, TLS handshakes and auth checks by 6×, and give one
   consistent snapshot.
5. **Longer history:** readings are kept 8 days, so history stops at 7. An hourly aggregate table
   (a few KB per day) would allow months/years, e.g. a "this month vs last month" view.
6. **CO₂ ventilation hint:** when CO₂ passes 1200 ppm, a push "open a window" (with the same
   once-per-excursion logic), and when it's back under 800, "you can close it".
7. **Widget battery** (#80) and exact alarms: the manifest asks for both `SCHEDULE_EXACT_ALARM` and
   `USE_EXACT_ALARM`; one is enough, and an inexact 5–15 min alarm would do for a widget.

**Web page**
8. **History on load:** the sparklines start empty on every page load (live MQTT only). The backend
   already publishes hourly occupancy retained; a retained "last 24 h" summary topic for
   temp/humidity/CO₂ would fill the charts immediately.
9. **Node list after the merge:** `NODES` still lists `co2_sensor_esp32` (B5).

**Firmware**
10. **OTA updates** (#79): every fix in this list needs a USB flash otherwise.
11. **E-ink partial refresh:** a full refresh every minute flashes the panel and wears it; GxEPD2's
    partial window for the minutes column, with a full refresh every ~30 min, is calmer.

## 7. Android lint

`./gradlew lintPreview`: 2 errors, 153 warnings. Most are noise or intended:

| Lint id | Count | Verdict |
|---|---|---|
| `UseAppTint` (error) | 2 | **False positive:** `widget_homeenv.xml` is a RemoteViews layout, where AppCompat's `app:tint` doesn't work; `android:tint` is right. Suppress with `tools:ignore="UseAppTint"` so `lint` can gate CI. |
| `UselessLeaf` | 3 | **Intended:** the widget's weighted spacer FrameLayouts (#96); RemoteViews can't use `Space` on older Android. `tools:ignore` too. |
| `UnusedResources` | 1 | **Real:** `R.string.app_name` is unused (the label comes from the `appLabel` manifest placeholder). Delete it. |
| `GradleDependency` | 5 | **Real:** see improvement 2 (core-ktx 1.12 → 1.19, appcompat 1.7 → 1.8, firebase-messaging-ktx → firebase-messaging, …). |
| `OldTargetApi` | 1 | `targetSdk = 34`. Fine for a sideloaded app; Play would require 35. Do it with the release build (#60). |
| `DataExtractionRules` | 1 | `allowBackup="false"` is enough for this app; add `dataExtractionRules` only if Play ever matters. |
| `HardcodedText` | 88 | Texts in layouts instead of `strings.xml`. Only matters for a translation; ignore or disable the check. |
| `UseCompatTextViewDrawableXml` | 30 | `drawableStart` → `app:drawableStartCompat` (tint/vector support on old Android; minSdk 26 makes it cosmetic). |
| `SmallSp`, `CustomViewStyleable`, `UnusedAttribute`, `DisableBaselineAlignment` | 12 | Cosmetic. |

With the two widget suppressions, `lintPreview` passes and could be added to the CI Android job.

## 8. Suggested order

1. B1 alert hysteresis (the only user-facing annoyance), together with the CO₂ ventilation push (6.6).
2. B4 non-blocking MQTT reconnect + B2/B3 firmware fixes, so they ride along with the e-ink
   reflash that #70/#53/#50/#75 already need.
3. S3 CI permissions + lint in CI + Dependabot (small, one PR).
4. Refactors 5.1–5.3 (backend helpers, SQL averages, pytest fixtures).
5. Features 6.4/6.5 (dashboard endpoint, long-term history) when you want new app work.
