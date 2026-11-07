# Full review — 2026-11-07

Scope: the whole codebase on `main` at `8b3b008` (#299): `backend`, `android/app/src/main`,
`web`, `firmware`, `.github`, checked against `/contracts`. It covers the fridge monitor
(#290, #294), notification history (#289, #296) and the reworked baseline alert (#297).
Read-only: nothing was changed. The previous report is `2026-10-19-review.md`.

Method: a full-repo code review pass, then each finding checked by hand against the code.
Findings that didn't hold up are left out; line numbers are the current ones.

## 0. State

- **Backend tests:** 51/51 pass (Python 3.14 venv, `requirements*.txt`); `ruff check backend` clean.
- **Firmware:** CI compiles `eink_node`, `main_node` and, since #294, `fridge_node`. The fridge
  node runs the #294 firmware (flashed 2026-11-07).
- **Server:** `/opt/iot` matches `main` (`deploy.sh --check`).
- **Branches:** only `main`, locally and on GitHub. Merged PR branches are deleted automatically
  on GitHub.
- **Open issues:** #229 (baseline tuning), parked until December.
- **Dependencies:** `paho-mqtt` 2.2.0 is out (2.1.0 pinned) and Dependabot hasn't proposed it.
- **Secrets:** no secret file was ever committed, and the history has only the placeholder
  `AKIAEXAMPLE…` keys. But see **M1**: the repo is **public**.

## 1. Findings

### High

| ID | Finding | Fix |
|---|---|---|
| H1 | **A fridge alert muted overnight is lost, and its "back to normal" still arrives.** `fridge.check_fridge_alerts()` saves `fridge_alert_state` for a muted alert exactly like a sent one. At 23:00 in quiet hours, `warm_low` is muted and saved, so nothing is shown and nothing is pushed when quiet hours end, even if the fridge is still warm. In the morning, `""` then sends `Fridge back to normal` for a warning that was never seen. The node-status, watchdog and baseline alerts all keep a separate muted state for this case. | Store `sent`/`muted` (as `baseline_alerted:` does). After a muted alert, push it once quiet hours are over if it's still on. Send the recovery only after a sent alert. A fridge warming up is worth pushing through quiet hours: consider exempting `warm_critical*` from quiet hours, as the occupancy alert is. |
| H2 | **A fridge node reboot gives a false all-clear.** The firmware keeps the condition timers in RAM. After a reboot (WiFi-timeout `ESP.restart()`, brownout, OTA), it publishes `alert: ""` for at least 30 min while the fridge is still at 9 °C. The backend then sends `Fridge back to normal`, and `Fridge too warm` again 30 min later. | On the backend, treat `""` from a node whose `uptime` is under the longest timer (2 h) as "unknown", not a recovery. Alternatively, keep the condition start times in RTC memory (`RTC_DATA_ATTR`, which survives software resets) or NVS. |

### Medium

| ID | Finding | Fix |
|---|---|---|
| M1 | **The repo is public, and a real IP is in it.** `backend/tests/test_issue61_api_auth.py:45` uses a real public address from an access log (probably the home connection). It has been in public history since `980767e` (2026-10-16). The other cases use `203.0.113.x`. `.github/workflows/firmware.yml` says "the private repo has 2,000 Actions minutes", which is no longer true either. This breaks CLAUDE.md: "Never hard-code … IPs". | Replace it with `198.51.100.x`. The address stays in the history unless the history is rewritten (`git filter-repo`), which is a separate decision. A changing home IP is low-risk, but the rule exists for a reason. Fix the workflow comment. Consider making the repo private again, or run a secret scanner (gitleaks) in CI now that it's public. |
| M2 | **The fridge recovery doesn't replace the alert on the phone.** The app picks the notification slot from `hash(event/device)` (`MyFirebaseMessagingService.kt`). The alert uses `FRIDGE_ALERT` and the recovery uses `FRIDGE_NORMAL`, so both stay on screen. `NODE_STATUS`, `SENSOR_STATUS`, `WATCHDOG` and `BASELINE` reuse one event for exactly this reason. | Send the recovery with `"event": "FRIDGE_ALERT"` and update `notifications.md`. |
| M3 | **The baseline compares UTC hours, but the household runs on local time.** `baseline.history()` groups by `strftime('%H', hour)` on UTC. After a DST change (2026-10-25), 06:00 UTC was 08:00 local for most of the 28-day window and is 07:00 local now. A morning heating ramp at a fixed local time lands an hour off against most of the history, giving inflated z and possible false pushes for up to 4 weeks after each switch. | Use `strftime('%H', hour, 'localtime')` for both the newest hour and the history (the server runs in Budapest time). |
| M4 | **"2 unusual hours in a row" can be two hours far apart.** `baseline_count:<metric>` is reset only by a checked normal hour. A stale hour (`return` after `BASELINE_MAX_AGE_HOURS`) or a null value skips it. So: hour 10 unusual, node down 11–15, hour 16 unusual, and a push goes out on the second one. | Store the hour of the last unusual reading with the count, and treat the next one as "in a row" only if it's exactly one hour later. |
| M5 | **The fridge and baseline checks run last in `check_watchdogs()`.** An exception in the earlier unguarded steps skips them for that cycle. Examples: `backups()` stat-ing a file that `backup_db.py` just removed, or `disk_usage` failing. Fridge pushes also wait up to 5 min behind unrelated server checks. The `try/except` around the two calls is redundant, because `send_*_alerts()` never raise. | Call them as their own guarded steps in `summaries_loop`, or check the fridge on ingest when its `alert` changes (no 5-min delay). |

### Low

| ID | Finding | Fix |
|---|---|---|
| L1 | **The presence handler doesn't validate `device`** (`ingest.py:57`). The env path enforces type and `DEVICE_ID_MAX` (#170). A presence payload with `"device": 42` makes `"hallway" in device` raise. A 10 KB string is stored as-is. | The same check as the env path. |
| L2 | **`?days=²` returns 500 instead of 400.** `str.isdigit()` accepts Unicode digits that `int()` rejects (`api.py:323`, `:392`). | `days.isascii() and days.isdigit()`, or `try: int()`. |
| L3 | **The fridge card says "normal" with no fridge data.** A missing `latest_all` entry gives `alert = ""` (`HomeFragment.renderFridge`). | Status `--` (or "no data") when `fridge == null`. |
| L4 | **`temp_c` from the fridge isn't rounded**, so it shows 0.0625 °C DS18B20 steps (`3.5625`). The contract says 1 decimal. | `roundf(tempC * 10) / 10` in the firmware, or change the contract. |
| L5 | **`/api/stats24h_combined` and the CO₂/pressure parts of `/api/hourly` and `/api/air` aren't room-filtered.** The fridge stays out only because it sends no humidity, CO₂ or pressure. A future outdoor node would leak in. | Add `ROOM_DEVICES_SQL` there too. |
| L6 | **The app hard-codes the fridge.** `HomeFragment` filters `key != FRIDGE` for the room card, duplicating `NON_ROOM_DEVICES`. | Have `/api/latest_all` mark each device `"room": true/false` (see R4). |
| L7 | **`state.reset()` leaves `PUSH_FAILURES`**, so retry back-off counts survive `prepare()`. This only matters in tests and on a double start. | Add it to the reset list. |
| L8 | **The `calibrate` serial command blocks `loop()` for 180 s** (`main_node.ino`). MQTT drops after ~90 s (the offline will fires), and the radar buffer overflows. It's rare and manual. | A non-blocking state machine, or at least `mqttClient.loop()` and `ArduinoOTA.handle()` in the wait. |
| L9 | **Dead code.** `initBme()` stub in `fridge_node.ino`. `firmware/fridge_node/ds18b20_test.ino.skip`. The baseline's 15-min cooldown can never apply, because it already runs once an hour. | Delete them. Drop the baseline cooldown check and document "retried on the next hour". |
| L10 | **A duplicate timestamp parser.** `NotificationHistoryFragment.parseTs` copies `HomeFragment.parseTs`. | Move it next to `optNum` in `Api.kt`. |

### Performance (fine at today's size, worth doing when touching the code)

| ID | Finding | Fix |
|---|---|---|
| P1 | **Retention `DELETE … WHERE ts < now-2d` runs on every insert** into `readings`, `presence_readings` and `events` (`store.py:223`, `:240`, `:407`). It's several statements per message, on tables with no index for a `ts`-only condition. | Prune once per `summaries_loop` pass (5 min). |
| P2 | **`any_radar_quiet_minutes()` scans `presence_readings` backwards for `occupied = 1` on every presence message.** With nobody home that's up to ~69k rows, every ~2.5 s. | Keep the last occupied time in memory (it's already computed per message). |
| P3 | **`metrics.latest_sensors()` filters by time only in the subquery**, so every scrape scans 2 days of `readings` four times. | Add `AND ts >= datetime('now', ?)` to the outer `WHERE`. |
| P4 | **One presence message opens 6–8 SQLite connections** (each helper calls `db()`). | Pass one connection through the handler: one transaction per message. |

## 2. Refactors

- **R1. One helper for "excursion" alerts.** Every bug in H1, M2 and #294–#297 comes from re-implementing the same state machine by hand. The pattern is a condition that starts and ends, plus:
  - once per excursion
  - muted vs sent
  - save only after a successful push
  - retry back-off
  - recovery only after a sent alert
  - the same `event` for the alert and its recovery

  `ingest` (humidity/temp), `server_checks._watch`, `nodes`, `air`, `fridge` and `baseline` each have their own copy. A small `Excursion(key, kind, event)` class in `alerts.py` with `start(title, body)` / `end(title, body)` would make H1 and M2 impossible. It would also shrink `on_message`, the biggest function at 135 lines.
- **R2. `init_db()` (144 lines) as a list of numbered migrations,** each run once and recorded in `system_state` (`schema_version`). It already has 5+ one-off "once" flags (`ts_utc`, `hourly_room_rebuilt`, the ALTER loop, …).
- **R3. Split `api.py` (644 lines) into Flask blueprints:** env/latest, history (`hourly`, `daily`), notifications (settings, history, arm), metrics/health. The routes are independent, and `contracts/api.md` is already organized that way.
- **R4. A device registry** in `config.py` (`{device: {"role": "room"|"fridge"|"radar", …}}`), replacing `PRESENCE_DEVICE`, `AIR_DEVICE` and `NON_ROOM_DEVICES`. It would be served in `/api/devices` so the app and the web page stop hard-coding device IDs (L6).
- **R5. `HomeFragment` (574 lines):** one small renderer per card (`EnvCard`, `FridgeCard`, `AirCard`, `ServerCard`), each with its own `render(json)`.
- **R6. Pull the fridge alert logic into the shared library** (`HomePing.h`): a `Condition` timer with hysteresis. The outdoor probe (memory: planned) will want the same.

## 3. Feature ideas

- **Fridge:**
  - Door-left-open detection: a rise of 2–3 °C within ~5 min, pushed after 10 min, well before the 30-min warm alert.
  - Today's min/max on the card.
  - The fridge on the History screen (7/30 days; the `hourly` table excludes it, so it needs its own roll-up or a `device` filter).
- **Notification history:**
  - Tap a row to open the matching screen.
  - A "show muted" toggle (the data is in `events`), so a night of muted alerts can be read in the morning. That's the visible half of H1.
- **Node health in the app:** a Devices screen with RSSI, uptime, free heap and last reset reason per node. The backend already has it all for `/api/metrics`. Today it's Grafana-only.
- **Outdoor probe** (planned): use it instead of Open-Meteo for the air advice and the "Close the window?" check, and as a third room-vs-outside comparison.
- **Weekly summary push** (Sunday evening): average temperature and CO₂, hours over 1200 ppm, fridge min/max, node restarts.
- **Backup restore drill:** a monthly job that restores the latest S3 backup into a temp DB and runs `validate_db.py` on it, so #122's off-site copy is proven restorable, not just uploaded.
- **Repeated-crash push:** two or more `RESTART` events with reason panic/brownout/watchdog from one node within 24 h.

## 4. Suggested order

1. **H1, H2, M2:** fridge alerts. These are the ones that can mislead about food safety.
2. **M1:** the IP and the "private repo" comment (a two-minute change, plus a decision on history and visibility).
3. **M3, M4:** before the December tuning of #229, or the data collected until then is skewed.
4. **R1:** the excursion helper, ideally together with 1, so the fix doesn't become copy number seven.
5. The rest as they come up.
