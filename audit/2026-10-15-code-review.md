# Code review, 2026-10-15

Read-only review of `origin/main` at `e631509` (after #56). Nothing was changed.
Out of scope, because open issues already cover them: the node merge and its firmware (#8, #10,
#19, #50, #53, #57), `latest_all` timestamps (#11), and MQTT security / port 5000 / TLS (#31).

Severity: **High** = fix soon, **Medium** = worth a PR, **Low** = cleanup / hardening.

**Tracked as issues:** S1 → #58 (fixed in #83) · S2/S3 → #59 · S4 → #60 · S5 → #61 · S6 → #62 ·
S7/S8 → #63 (eink certificate → #70) · B1 → #64 · B2 → #65 · B3/B4 → #66 · B5 → #67 ·
B6 → comment on #57 · dead code → #68 (backend), #69 (Android), #70 (eink), #71 (repo) ·
refactoring → #72–#75 · ideas → #76–#82. Git sync: branches cleaned up the same day.

---

## 1. Git sync

| Check | Result |
|---|---|
| Local `main` vs `origin/main` | **Behind by 2** (#56 merge). Run `git pull`. |
| Local branches whose remote is gone | **23** (all merged PRs). Safe to delete locally. |
| Extra branches on GitHub | `chore/line-endings` and `fix/16-firmware-build`: already in `main`, can be deleted. `feat/30-widget`: the shelved first widget (#34, unmerged), superseded by #36. Delete or keep for reference. |
| Stash / extra worktrees | None |
| Ignored local files | Secrets present and correctly ignored (`firmware/*/secrets.h`, `web/config.js`, `android/local.properties`, `google-services.json`). Junk: `android/gradle/wrapper/daemon/` (ignored, can be deleted). |

---

## 2. Security

### S1 (High): one malformed MQTT message stops all data ingestion
`backend/server.py:681-683` decodes and prints the payload **before** the `try`. A payload that
isn't valid UTF-8 (`b"\xff\xfe"`) raises `UnicodeDecodeError` out of `on_message`. Verified with a
test. paho re-raises callback exceptions by default (`suppress_exceptions = False`), so the
exception ends the `loop_start()` thread. From then on nothing is stored or alerted, while the
REST API keeps answering with old data. The only fix is a restart.
Anyone who can publish to `home/esp32/#` can trigger it, including the leaked `esp32` account until #31 is done.
**Fix:** move the decode/print into the `try` (or `decode(errors="replace")`), and wrap the
whole callback so that no exception can escape.

### S2 (Medium): the backend runs as root
The systemd unit has `User=root`. Any bug in Flask/paho/firebase code runs with full system rights.
**Fix:** a dedicated `iot` user that owns `/root/iot` → `/opt/iot` (DB, service-account JSON, `.env`).

### S3 (Medium): Flask development server in production
`app.run()` is Werkzeug's dev server (the journal warns about it on every start). It's not hardened
for direct exposure. **Fix:** `waitress` or `gunicorn` (one worker, because of the MQTT thread),
bound to `127.0.0.1` behind nginx. This combines well with closing port 5000 (#31).

### S4 (Medium): the APKs are debug builds with the API password inside
`homeenv.apk` is `assembleDebug`: debuggable, signed with this PC's debug key, no R8. `BuildConfig.API_PASS`
can be read from the APK with any decompiler. The files in `D:\homeenv-apks\` therefore contain
the password and must be treated like secrets (never share or upload them).
**Fix:** a release build type with its own keystore (`keystore.properties` is already gitignored)
and R8. It doesn't remove the password from the APK, but it's no longer debuggable.

### S5 (Low): Basic auth: timing-unsafe compare, no rate limit
`require_auth` compares with `!=`. Use `hmac.compare_digest`. There's no lockout or rate limit on
`/api/*`. **Fix:** nginx `limit_req` on `/api/`, or a fail2ban jail for 401s (fail2ban is already
installed).

### S6 (Low): web page loads `mqtt.min.js` from unpkg without SRI
`web/index.html:8`. If the CDN were compromised, the script would see the MQTT login from
`config.js`. **Fix:** add an `integrity="sha384-…" crossorigin` attribute, or self-host the file
next to `index.html`.

### S7 (Low): the widget receiver accepts the custom action from any app
`AndroidManifest.xml`: `HomeEnvWidget` is `exported="true"` (the launcher needs that) and lists
`com.david.homeenv.AUTO_UPDATE` in its intent filter. Any installed app can trigger fetches.
The alarm uses an explicit `PendingIntent`, so the action doesn't need to be in the filter.

### S8 (Low): other hardening
- `android:allowBackup="true"`: nothing sensitive is stored at runtime today. Set `false` or add rules.
- eink_node fetches BKK over HTTPS **without certificate checking** (`HTTPClient.begin(url)`, no CA).
  Low impact (tram times), but cheap to fix with `setCACert`.

---

## 3. Bugs and robustness

### B1 (Medium): the notification icon is a white blob
`MyFirebaseMessagingService` uses `R.mipmap.ic_launcher` as the small icon. Android renders small
icons as a white silhouette, so a full-colour launcher icon becomes a white square/circle in the
status bar. **Fix:** a monochrome vector `ic_notification`.

### B2 (Medium): local timestamps without time zone break at DST changes
Every `ts`/`last_seen` is a server-local string (`datetime('now','localtime')`).
- **25 Oct (fall back):** the hour 02:00–03:00 happens twice. Hourly buckets merge two hours,
  and string ordering of `ts` isn't chronological for that hour.
- **Spring forward:** `now - last_seen` jumps by an hour for anything last seen before the jump.
  The new offline check (#54) can send a false "Node offline" / "back online" pair. This is most
  likely for the CO₂ node, which reports every 60 s.
- The app compares server-local strings with the phone's clock (fine only while both are in Budapest).

**Fix (bigger):** store UTC, and convert to local time only at the API and display edges.

### B3 (Low): MQTT callback API v1 is deprecated
The journal shows `DeprecationWarning: Callback API version 1 is deprecated`. Move to
`mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)` (changed `on_connect`/`on_disconnect` signatures)
before paho drops v1.

### B4 (Low): log volume
Every MQTT message prints 2–3 lines (`[MQTT] Received…`, `Payload…`, `[PRESENCE]…`). That's about
**100k journal lines a day**, which makes real errors hard to find and costs disk. Use `logging`
with levels: payloads at DEBUG, alerts and errors at INFO/WARNING.

### B5 (Low): app details
- The occupancy switch reverts silently when the request fails. Show a short message.
- The armed state is loaded only in `onViewCreated`, not on resume, so a change made elsewhere
  isn't picked up until the app restarts.
- `MainActivity` opens the exact-alarm settings screen on **every** launch if the permission is
  missing. Sideloaded builds get `USE_EXACT_ALARM` automatically, so this rarely shows, but it
  would loop on a phone that denies it.

### B6 (hardware, for the merge): sensor accuracy
- **MH-Z19 auto-calibration** (`mhz.autoCalibration(true)`) assumes the lowest reading of each
  24 h is 400 ppm (fresh air). A room that's never fully aired drifts *low* over weeks. Either air
  the room daily or switch ABC off and calibrate outdoors once.
- **BME280 self-heating:** on the shared board the BME280 will sit near the MH-Z19 (its lamp is
  heated), the ESP32 and the radar. Keep it as far away as possible, and expect to set `TEMP_OFFSET`
  by comparing with a reference thermometer.

---

## 4. Dead code and leftovers

**Backend (`server.py`)**
- Unused imports: `re`, `timedelta`.
- Unused constant: `CONFIDENCE_THRESHOLD = 65`.
- Unused parameter: `handle_presence_data(data, topic)`, where `topic` is never read.
- Stale comments: `UPTIME_DROP_TOLERANCE … see note below` (there is no note), and
  `# ---- add near the other config constants, top of the file ----` (a leftover instruction).
- `firebase_admin._apps` is a private attribute. Use `firebase_admin.get_app()` in a try/except.

**Android**
- Unused resources: `drawable/status_pill.xml`, `drawable/widget_bg.xml`,
  `drawable/widget_bg_gradient.xml`, `drawable/widget_card_bg.xml`, `layout/widget_bg_gradient.xml`
  (a drawable in the layout folder), and colours `black`, `white`.
- Unused dependencies: `androidx.work:work-runtime-ktx`, `androidx.cardview:cardview`.
- Unused permission: `WAKE_LOCK`.
- `HomeEnvWidget.scheduleNextUpdate`: with `minSdk 26`, the "Android 6–11" branch is the only
  pre-12 path and the "Android 5 and below" `setExact` branch is unreachable.
- `AndroidManifest.xml` still has `package="…"` (the namespace now lives in Gradle; AGP warns).
- Root `build.gradle.kts`: a `buildscript` classpath for google-services with the comment
  "add this if missing". Move it to the `plugins {}` block like the others.

**Firmware (`eink_node.ino`)**
- Never used: `getCurrentHour()`, `drawClockIcon()`, `#include <Fonts/FreeSansBold24pt7b.h>`, `#include <math.h>`.
- The route filter (`BKK_3022`, `BKK_3510`) is hard-coded in the sketch, but the same route IDs
  are also in `BKK_URL` in `secrets.h`. Two places to change.

**Repo**
- `.gitkeep` files in folders that have content (`backend/`, `contracts/`, `firmware/`, `web/`);
  `docs/` is empty.
- `README.md` is two lines and uses the old name ("esp32-system").

---

## 5. Refactoring candidates

1. **Backend structure:** one 1,160-line module that connects to the DB, the MQTT broker and
   Firebase and starts threads *at import time*. Every test has to stub three libraries to import it.
   Split it (config / db / alerts / mqtt ingest / api) and start everything from `main()`.
2. **Backend config:** constants are spread over four places (lines 44–66, 319, 478, 673–679), some
   defined *after* the functions that use them. Group them; thresholds could come from `.env`.
3. **DB boilerplate:** 23 copies of `conn = sqlite3.connect(DB) … conn.close()`. A small context
   manager helper would remove the boilerplate and the risk of a missing `close()` on an exception.
4. **Android networking:** `HomeFragment`, `HomeEnvWidget` and `PushRegistration` each build their
   own OkHttp client and Basic-auth header. One `Api` object (one client, timeouts, auth) would
   remove the duplication.
5. **Android views:** 38 `findViewById` calls in `HomeFragment`; ViewBinding would remove them.
6. **Duplicated thresholds:** the level bands (CO₂ 800/1200/2000, humidity 30/40/60/70, temperature
   18/20/24/27) live in `Levels.kt`, `WidgetGauge.kt`, `Co2GaugeView.kt` (fractions) and
   `web/index.html`. Changing one band means four edits.
7. **Duplicated gauge drawing:** `Co2GaugeView` and `WidgetGauge` draw the same bar and needle.
   One shared drawing function would cover both.
8. **Firmware:** `connectWiFi`, `connectMQTT` and `publishPresence` are copy-pasted across sketches.
   After the merge (two sketches left), a small shared header in `firmware/common/` would keep them
   in sync.
9. **Contracts:** all four files still say "DRAFT, reverse-engineered … on branch `contracts-draft`
   (2026-10-13)", and the mismatch lists are growing with ~~struck-through~~ entries. Drop the draft
   header, and move the fixed entries to a short changelog at the end.

---

## 6. Improvement ideas

1. **Tests in the repo + CI.** About ten backend test scripts were written during the last fixes
   (#5, #9, #12–#15, #18, #13), but they live outside the repo. Commit them as `backend/tests/`
   (pytest) and add a GitHub Actions workflow: pytest, `arduino-cli compile` for each sketch with
   `secrets.example.h`, and `./gradlew assemblePreview` with `local.properties.example`. All three
   already work with the example configs.
2. **Watch the watcher.** All alerts, including "Node offline", come from the backend. If the
   backend, nginx or the VPS dies, nothing tells you. Add an `/api/health` endpoint, without data,
   and point an external uptime monitor (e.g. UptimeRobot, free) at it.
3. **Database backups.** `data.db` isn't backed up. A nightly `sqlite3 data.db ".backup …"`
   with a few days of rotation.
4. **OTA firmware updates** (ArduinoOTA with a password). Reflashing would no longer need USB,
   which helps now that several firmware changes are queued. Check the partition scheme: eink_node
   is at 90 % of the app partition.
5. **Widget battery.** The 2-minute alarm fetches even with the screen off. Skipping the fetch when
   `PowerManager.isInteractive()` is false (but still rescheduling) would cut most wake-ups. The
   widget is fresh again within 2 min of unlocking.
6. **Web page: live node status** from the new retained `home/esp32/<device>/status` topic (#55),
   without polling the API.
7. **App:** an "offline / can't reach server" banner; history beyond today (e.g. `/api/hourly?days=7`);
   per-alert switches or quiet hours for notifications.
8. **Logging + rotation** (see B4), and a structured `[EVENT]` log line per alert, for easy grepping.
