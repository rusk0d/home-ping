# Full review — 2026-11-08

Scope: the whole codebase on `main` at `2472592` (#348): `backend`, `android/app/src/main`,
`web`, `firmware`, checked against `/contracts`. Since the previous report
(`2026-11-07-full-review.md`, at `8b3b008`) 27 PRs were merged: the excursion helper, numbered
migrations, the API blueprints and the device registry (#305/#306), the Devices screen, the
fridge history, the door push and the restore drill. Read-only: nothing was changed.

Method: every backend module read in full; the app, firmware and web page read where they
changed or handle alerts. Each finding below was checked against the code by hand, and line
numbers are the current ones.

## 0. State

- **Backend tests:** 62/62 pass, and `ruff check` is clean.
- **Server:** `/opt/iot` matches `main` (`deploy.sh --check`). The restore drill passed on the
  server on 2026-11-08.
- **Open issues:**
  - #229: baseline tuning, parked until December.
  - #307: feature ideas. Every item is ticked; it can be closed.
  - #334: outdoor probe.
- **Last review:** the items from it are fixed, with one exception:
  - H1, H2 and M2 in #300; M1 in #301; M3 and M4 in #302; M5 and the Lows in #303, #304
    and #306.
  - The refactors R1–R6 are done (#305, #306).
  - The exception is one leftover from L9: `firmware/fridge_node/ds18b20_test.ino.skip` is
    still there.
- **Housekeeping:** 10 merged `feat/*` branches are still in the local checkout. GitHub has
  already deleted them.

## 1. Findings

### High

| ID | Finding | Fix |
|---|---|---|
| H1 | **A node that goes offline during quiet hours is never pushed.** `nodes._outage()` (`nodes.py:322`) builds its `Excursion` without `postpone_quiet`. So at 23:00 the offline push is *muted*, `node_offline:<device>` is saved as `muted`, and `check_nodes()` skips the device from then on (`if device in state.NODE_OFFLINE: continue`). At 07:00 nothing is pushed, though the node is still down. The watchdog and the silent-sensor check postpone through quiet hours for exactly this reason (`notifications.md` §7, §8), but node offline (§6) doesn't. It matters most for the **fridge node**: while it's offline there are no fridge alerts at all, so a dead fridge node at night means no warning about the node and none about the fridge. The only sign is the red dot in the app. | `postpone_quiet=True` in `_outage()`. During quiet hours `start()` then returns None, `NODE_OFFLINE` stays unset, the 30 s check tries again and pushes after quiet hours. A node back before 07:00 never gets a push, which is right. Update §6 of `notifications.md`. Test: offline at 23:00 gives no push; at 07:00 it pushes; back at 06:00 gives nothing at all. |

### Medium

| ID | Finding | Fix |
|---|---|---|
| M1 | **A fridge that warms up at night is reported at 07:00.** The fridge alerts are muted in quiet hours, then pushed once they end (#300). `warm_critical` at 23:30 (above 8 °C for 30 min) therefore arrives about 7.5 h later. The previous review suggested exempting it, as the occupancy alert is, but that wasn't done. Food safety is the one case where a 3 a.m. push is worth it. | Your call. If yes: `warm_critical` and `warm_critical_duration` ignore quiet hours but still respect the `fridge` switch, and `cold` / `warm_low` stay as they are. In `alerts.alert()`, a `kind="fridge_urgent"` that `alert_muted()` checks only against `notify:fridge`. Update the settings text "Below 0 °C, or above 5 °C…". |
| M2 | **The DS18B20's 85 °C power-on value gets through.** The DS18B20 returns exactly 85.0 °C after a brownout or a glitch on the data line. The firmware drops only `DEVICE_DISCONNECTED_C` (-127, `fridge_node.ino:65`), and the backend's range is inclusive (`ENV_VALID temp_c (-40, 85)`, `lo <= v <= hi`, `ingest.py:209`). One such reading is stored and lands in `hourly.fridge_max` (kept for good). It shows as "today 3.9–85.0°" on the fridge card and as a spike in the History chart. It also starts a door rise, though that one ends on the next reading without a push. | Firmware: skip `tempC == 85.0` like -127. Backend, for the nodes already flashed: for the fridge device, drop `temp >= 85` (or use a plausible range of -30…50 °C for a fridge). |

### Low

| ID | Finding | Fix |
|---|---|---|
| L1 | **A server watchdog entry in the notification history opens Home.** `Screens.forEventType` (`Screens.kt:165-169`) sends `WATCHDOG_*` to `MainActivity` ("their cards on Home"), but the Server card moved to Devices in #325. | Add `WATCHDOG_` to the Devices branch and fix the comment. |
| L2 | **Tapping a push always opens Home.** `MyFirebaseMessagingService.sendNotification` (`:111`) builds one fixed intent to `MainActivity`. The notification history already routes by type (`Screens`, #321/#329), so a fridge alert in the shade goes to Home while the same row in the history goes to the fridge chart. | Map the push's `data.event` to a screen: `FRIDGE_ALERT` / `FRIDGE_DOOR` / `TEMP` / `HUMIDITY_*` / `PRESSURE` to their charts, `NODE_STATUS` / `SENSOR_STATUS` / `WATCHDOG` / `RADAR_DISCONNECTED` to Devices. A distinct `requestCode` per slot (the notification id), or `FLAG_UPDATE_CURRENT` makes them all share the last intent. |
| L3 | **A broken backup can look fresh.** `backup_db.backup()` (`:47`) gzips straight into the final `data-<date>.db.gz`. If that fails halfway (disk full, killed), a truncated file with a new mtime is left behind, and the backup watchdog counts it as tonight's backup. The off-site check notices only when a bucket is set, since the upload isn't attempted. | Gzip to `data-<date>.db.gz.tmp`, then `os.replace()`, as `write_result()` in the drill does. |
| L4 | **The drill doesn't look at the backup's age.** It restores the *newest* S3 object, whatever its date. If uploads stopped two weeks ago, the drill restores that old file and reports OK, and the app shows "Restore drill · OK". The `offsite` watchdog covers this, but only while the `nodes` switch is on. | Fail (or at least say so in `error`) when `uploaded` is more than ~2 days before the drill ran. |
| L5 | **`deploy.sh` restarts the backend for `backup_drill.py`.** The skip list (`deploy.sh:127`) has `backup_db.py` and `test_push.py` but not the drill script, which the backend never imports. | Add `backup_drill.py`. |
| L6 | **A bad presence field drops the whole message.** `int(data.get("distance", 0) or 0)` (and `signal`, `rssi`, `ingest.py:63-65`) raises on `"far"` or `{}`. The message is then lost before `log_presence` / `touch_device`, so a firmware bug in one field looks like a node going offline. | Use `read_int()` (None → 0) for those three, as for `uptime`. |
| L7 | **A retired node stays in the red dot for 30 days.** `DeviceIssues.list` lists every device in `/api/devices` that isn't online. The backend keeps a device for `DEVICES_RETENTION_DAYS` (30) after its last message, so a node taken out of service keeps the devices button red for a month. | Only nodes in `config.DEVICES` (the registry) count as an issue when offline, or ones seen in the last ~7 days. The backend could mark that in `/api/devices` (`known: true`). |
| L8 | **The web page shows the fridge node half-known.** It isn't in `NODES` (`web/app.js:14`), so it appears under its raw ID, and it isn't subscribed to `home/esp32/fridge_temp`, so "online" comes only from the retained status topic, never from data age. | Add it to `NODES` (`staleAfter: 180`) and subscribe for freshness. A fridge value on the page is a feature (see §3). |

## 2. Checked and fine

These were looked at specifically and hold up:

- **`Excursion`:** the start, band and cooldown order is correct. A failed push isn't saved, and a recovery goes out only after a sent start. Its state is only written from one thread per key: MQTT (humidity, temperature, door), summaries (watchdog, pressure, baseline, fridge), node check (offline under `NODE_LOCK`, silent sensors).
- **Migrations:** numbered, each committed with its version, so a crash re-runs only the unfinished one. `_legacy_version` maps the old flags. Migration 5 only fills the fridge columns, and migration 3 rewinds the roll-up so the rebuilt hours are correct (this is what the drill hit, #347).
- **`rollup_hourly`:** the room, occupancy and fridge parts go into one `INSERT OR REPLACE` per hour, so no column is lost. The last hour is redone, and `prune()` always runs after the roll-up.
- **Door rule:** it runs after `log_to_db`, so it sees the current reading. Its start is the newest of the lowest readings (#336). A short opening ends silently, and the rise state persists across restarts.
- **Restore drill:** never touches `data.db`. The started copy runs in its own folder (`cwd` + `config.DB` relative), the result file is atomic, and the S3 key is read-only.
- **API:** the auth check is constant-time, metrics use their own token, and `?days` is checked with `isascii()`. Each `/api/dashboard` part runs in its own request context. The room filter is applied everywhere (L5 of the previous review).

## 3. Feature ideas

- **Fridge door on the card:** "door open?" in the fridge card's status while the door push is active. `/api/latest_all` could carry `fridge_door` from `system_state`.
- **Fridge on the widget:** a small fourth value, or at least the alert colour, so a warm fridge shows without opening the app.
- **Fridge on the web page:** the page already gets the retained `fridge_temp` topic through `home/esp32/#` and just needs to show it (it would also fix L8).
- **Door limits:** tune them once there's a real opening. Run `tools/fridge_door_replay.py` within 2 days of it, because the raw rows are pruned after that.
- **Close #307**, because every item on it is done.

## 4. Suggested order

1. **H1:** a one-line fix with a test, and it closes the last way the fridge can fail silently.
2. **M1:** needs your decision first. **M2:** firmware plus a backend guard.
3. **L1, L2:** the app, in one PR.
4. **L3–L6:** backend and tools, small.
5. **L7, L8** and the features, when convenient.
