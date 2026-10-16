# Code review 2 — 2026-10-16 (evening)

Scope: the whole repository at `main` = `54c88ea` (after #166), plus read-only checks on the
production server (DB queries on a read-only connection, logs, ports, file modes). Nothing
was changed. The earlier reviews are `2026-10-15-code-review.md` and `2026-10-16-code-review.md`.

## 0. Sync and housekeeping

- Local `main` and `origin/main` are identical (0 ahead / 0 behind), the working tree is clean,
  and there are no open PRs.
- Leftover branches, all already merged: 8 local (`docs/31-drop-web-account`, `feat/11-app-co2-source`,
  `feat/133-eink-partial`, `feat/57-co2-bme280`, `feat/57-merged-node`, `fix/118-eink-departures`,
  `fix/57-env-transition`, `web/57-merged-node`) and 2 remote (`origin/docs/31-drop-web-account`,
  `origin/web/57-merged-node`). They can be deleted.
- `firmware/co2_node/` exists locally only, holding an ignored `secrets.h` (WiFi password and the
  deleted `esp32` login). The sketch itself was removed in #145. Delete the folder.

## 1. Open issues: status, to avoid duplicates

| # | Status | Note |
|---|---|---|
| #10 | **body outdated** | `/api/presence/latest` and `/history` no longer exist; the `any_radar_quiet_minutes` part was fixed in #101. Still valid: both radars share the retained topic `home/esp32/presence`, and `/api/hourly` / `hourly` occupancy only use `home_ping_esp32`. |
| #19 | valid | Radar units and MH-Z19 `err` still unchecked on hardware. Related: **B1** below. |
| #31 | **checklist outdated** | Everything except TLS is done (accounts split 2026-10-16, `esp32`/`web` deleted). The open "verify `web` can't publish" step refers to a deleted account. Suggest: close, and keep TLS as its own issue (or retitle). |
| #57 | **nearly done** | Remaining: soldering the radar RX/TX (hardware) and the #19 check. The e-ink items (#50, #53, #8, `sensor` login) were done in #133/#148. Suggest closing with a final note. |
| #60, #79, #80, #122, #126, #130, #131 | valid | Not repeated below. |

## 2. Security

| ID | Sev. | Finding | Suggested fix |
|---|---|---|---|
| S1 | medium | **No security headers** on `www.ruskodavid.hu`: no CSP, HSTS, `X-Content-Type-Options` or `frame-ancestors`, and `Server: nginx/1.18.0 (Ubuntu)` is exposed. The page keeps the MQTT password in `localStorage` with "remember me", so script injection is the risk that matters. The page itself is careful (no `innerHTML`, SRI on mqtt.js, `textContent` everywhere). | nginx: `server_tokens off;`, `add_header Strict-Transport-Security "max-age=31536000" always;`, `X-Content-Type-Options nosniff`, `Referrer-Policy no-referrer`, and a CSP: `default-src 'self'; script-src 'self' https://unpkg.com; connect-src 'self' wss://www.ruskodavid.hu; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`. The inline `<script>` must first move to `web/app.js` (or get a hash). |
| S2 | low | **Server leftovers:** `/root/iot` still holds the old code, a copy of `data.db`, the **Firebase service-account key** and old backups (the planned "delete after a few days" from #59). The ufw rule `5000/tcp ALLOW Anywhere` is stale; nothing listens publicly on 5000 since the backend binds 127.0.0.1. Mosquitto's 9001 listener binds all interfaces, and only ufw keeps it closed, although nginx is its only client. | `rm -rf /root/iot` (after one last look). `ufw delete allow 5000/tcp`. `listener 9001 127.0.0.1` in `mosquitto.conf`. |
| S3 | low | `/opt/iot/data.db` is `644` and `backups/` is `755`: any local user can read the readings and occupancy history. | `UMask=0077` in both systemd units, then a one-time `chmod 600 /opt/iot/data.db /opt/iot/backups/*; chmod 700 /opt/iot/backups`. |
| S4 | info | Two other projects on the same VPS run **as root** (`/traffic/server.js` under PM2 on `*:3000`, blocked by ufw; `/scrapper/app.py` "jobdash" on 127.0.0.1:4999). A hole in either gives root, and with it every Home Ping secret. Outside this repo; mentioned for awareness. | Own users plus systemd hardening, as done for `iot-backend` in #59. |

Already tracked: plain MQTT on 1883 (#31 TLS), the API password inside the APK (#60), backups on the same disk (#122).

## 3. Bugs and correctness

| ID | Sev. | Finding | Suggested fix |
|---|---|---|---|
| B1 | **medium** | **CO₂ readings are offset low.** Hourly minima in the `hourly` table: Oct 11 356, Oct 12 371, Oct 14 **261**, Oct 16 341 ppm. Indoor air can't drop below outdoor air (~420 ppm), so the sensor under-reads, probably by 60–160 ppm. Cause: `mhz.autoCalibration(true)` (ABC) treats the lowest reading of each 24 h as 400 ppm, which only works if the room reaches fresh-air levels daily; here the daily averages are 900–1250 ppm. Everything built on CO₂ (bands, #130 push, history) inherits the error. | Turn ABC off (`autoCalibration(false)`) and do a manual zero-point calibration: the sensor 20 min outdoors or by a wide-open window, then `mhz.calibrate()` (400 ppm). Repeat every few months, or keep ABC only if the flat is aired to outdoor level every day. Add a check to #19. |
| B2 | low | `/api/dashboard` passes its query string to the parts: `part()` calls the views inside the same request, so `/api/dashboard?days=7` (or `?device=`) changes the `hourly` part. Not used today, and not in the contract. | Call each part in its own `app.test_request_context(path)`, or reject query parameters on `/api/dashboard`. |
| B3 | low | An env payload without `device` is stored as `"esp32"`, a default from before the node merge. It would create a phantom device, and an offline push 5 min later. | Default to `PRESENCE_DEVICE`, or drop (and log) payloads without `device`. Contract: `device` required. |
| B4 | low | `POST /api/register_token` with a non-string `token` (e.g. a number) raises inside firebase-admin and returns a 500. The endpoint is legacy since #153. | Remove the endpoint once the second phone runs the #153 app (see E1). |
| B5 | low | `TEMP_OFFSET = 0.0` in `main_node.ino` is still the placeholder. The BME280 sits next to the heated MH-Z19, and the comment says it reads high. Temperature drives the low-temperature push and the daily stats. | Compare with a reference thermometer for an hour, then set the offset. |
| B6 | low | eink_node: `fetchAllDepartures()` blocks `loop()` for the TLS handshake plus up to the 10 s HTTP timeout every minute, and the radar backlog is then thrown away. So the hallway radar has a 1–12 s gap every minute. | `http.setTimeout(4000)`; publish presence right before the fetch. Fully fixing it means moving the fetch to its own FreeRTOS task. |

## 4. Efficiency and refactoring

| ID | Value | Finding | Suggested change |
|---|---|---|---|
| E1 | medium | **Backward-compatibility code that's dead now** (backend and app are deployed together, and only these two phones exist). Android: `legacyServerTime` (pre-#65 timestamps), the `co2_sensor_esp32` constant and the `env_ts`/`co2_ts` fallbacks (pre-#11/#57), the six-request fallback in `HomeFragment.loadSnapshot()` (pre-#128, the backend is live now), and the `notification` block fallback in `MyFirebaseMessagingService` (pre-#12). Backend: `/api/register_token`, once the second phone has the #153 app. | Remove in one Android PR and one backend PR; contracts updated. |
| E2 | medium | **Raw retention vs. the new `hourly` table.** `presence_readings` (~175k rows) and `readings` make up most of the 56.5 MB DB, and 27 % of the pages are free (3,691 of 13,802; never vacuumed). The 8 days of raw data exist only for `/api/hourly?days=7` (the History week view), which could now come from `hourly` (+ today from raw). | Week view from `hourly`; raw retention down to 2 days; one `VACUUM` afterwards. The DB and nightly backups shrink by ~4×. |
| E3 | low | Each radar message (2 radars × every 5 s, ~35k/day) opens 6–8 separate SQLite connections (`any_radar_quiet_minutes`, `log_presence`, `touch_device`, `note_uptime`, `get_last_occupancy`, `get_state`, …), each with its own commit/fsync in rollback-journal mode. Measured: every query < 1 ms and no "database is locked" in 7 days, so it's not a problem yet. | `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` (set once in `init_db`), and one connection per message. |
| E4 | low | The widget makes 2 requests (`/api/latest` + `/api/air`) every 2 min, and the home screen makes a 7th (`/api/occupancy_status`) next to `/api/dashboard`. | Add `occupancy_armed` to `/api/dashboard`; give the widget one call (e.g. `/api/widget` or `air` fields in `/api/latest`). Fits with #80. |
| E5 | low | Stale docs: `backend/test_push.py` says `python3 test_push.py` and "send_push() in server.py" (now `sudo -u iot venv/bin/python test_push.py`, `alerts.py`). The README's `/root/iot` migration section can go once S2 is done. | Text fixes. |
| E6 | low | CI has no Python linter; unused imports and similar slip through review. | `ruff check backend` in the backend job (dev dependency only). |

## 5. Suggestions and new features

Not covered by open issues (#130 CO₂ push, #131 daily summary, #122 off-site backups, #79 OTA, #80 widget):

| ID | Idea | Notes |
|---|---|---|
| F1 | **Backup and health watchdog push**: a push when the nightly `iot-backup` fails, or when free disk on `/` drops below e.g. 1 GB. | `OnFailure=` unit that runs a small push script (like `test_push.py`); disk check in the summaries loop. UptimeRobot only sees `/api/health`. |
| F2 | **Dew point / mould risk**: compute the dew point and absolute humidity from temperature + humidity; show them in the app, and optionally warn when humidity stays above 60 % for hours. | Backend-side, so the web page and the app agree; no new hardware. |
| F3 | **Level bands in the history charts**: faint 800/1200 ppm lines in the CO₂ chart, and the 40–60 % comfort band in the humidity chart. | Bands from `ui/Levels.kt` (#127). |
| F4 | **CSV export** of the `hourly` table (`/api/export?days=`), for spreadsheets and as a human-readable second backup. | Small; contract update. |
| F5 | **Airing feedback**: detect the fast CO₂ drop when a window opens, and show "aired 12 min ago, CO₂ 1300 → 650". It pairs with #130 ("you can close it"). | Needs B1 fixed first, otherwise the levels are off. |

## 6. Priorities

1. **B1** (CO₂ calibration): a real data-quality problem, and #130 would build on it.
2. **S1 + S2** (nginx headers, server leftovers): small, server-side.
3. **E1 + E2** (dead code, raw retention): less code and a 4× smaller DB.
4. Issue hygiene: update #10, close/retitle #31, close #57.
5. The rest when convenient; F1 is the most useful of the features.

## 7. Follow-up (same evening)

Issues created from this review:

| Finding | Issue |
|---|---|
| B1 + B5 sensor calibration | #167 |
| S1 security headers | #168 |
| S2 + S3 server cleanup, permissions | #169 |
| B2 + B3 dashboard query string, default device | #170 |
| B6 eink fetch blocks the loop | #171 |
| E1 + B4 dead compatibility code, `/api/register_token` | #172 |
| E2 week view from `hourly`, 2-day raw retention | #173 |
| E3 WAL mode | #174 |
| E5 + E6 docs, ruff in CI | #175 |
| F1 watchdog pushes | #176 |
| F2 dew point / mould risk | #177 |
| F3 bands in the history charts | #178 |
| F4 CSV export | #179 |
| F5 airing feedback | #180 |

E4 was added to #80 as a comment, and S4 (other projects on the VPS) is noted in #169.
Existing issues: #10 got an update note, #31 was retitled to TLS only, #57 was closed,
and #19 links #167. The 10 merged branches and the local `firmware/co2_node/` were deleted.
