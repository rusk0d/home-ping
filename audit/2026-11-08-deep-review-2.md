# Deep review 2 — 2026-11-08

Scope: `main` at `9bd0704` (#391). This is the third pass today, after `2026-11-08-full-review.md`
and `2026-11-08-deep-review.md`. This one hunts for edge cases rather than reading for
structure:
- every backend module again: ingest, alerts, nodes, store, fridge, air, baseline,
  server_checks, metrics and the API blueprints;
- the paths a node takes when the network or a sensor fails, checked against the ESP32 core
  the sketches build with (`esp32:esp32` 3.3.12).

Read-only: nothing was changed. Line numbers are the current ones.

## 0. State

- **Backend tests:** 68/68 pass, and `ruff check` is clean.
- **Server:** `/opt/iot` matches `main`.
- **Nodes:** all three were flashed with #388/#389 tonight and are online.
- **Previous report:** every finding is merged: #383/#384 in #388/#389, #385/#386 in #390,
  #387 in #391.
- **Open issues:**
  - #229: baseline tuning, December.
  - #334: outdoor probe.

## 1. Findings

### Medium

| ID | Finding | Fix |
|---|---|---|
| M1 | **A fridge door left open in the evening is never pushed.** The door push is an `Excursion` under the `fridge` switch with the default quiet-hours handling (`fridge.py:127`, `notifications.md` §11b), so quiet hours *mute* it: there's no postpone and no exemption. Say the door is left ajar at 22:30, with quiet hours on: the open is saved as `muted` and `start()` never tries again during that rise. The only push that night is "Fridge too warm" (#351), once the fridge has been above 8 °C for 30 min, which is an hour or more later. This morning's decision for "too warm" (food safety beats quiet hours) applies here even more: the door push is the early, actionable warning, and by 07:00 it's too late. | Your call. Two options: (a) the door push ignores quiet hours like `warm_critical`: `Excursion` gets an `ignore_quiet` option, which it passes to `alert()`; or (b) it's postponed (`postpone_quiet=True`), so it at least arrives at 07:00 if the door is still open. (a) matches #351. Update §11b and the settings text, which says "Fridge too warm still comes through". |

### Low

| ID | Finding | Fix |
|---|---|---|
| L1 | **An internet or server outage stalls the nodes' loop for up to 30 s per MQTT attempt.** None of the sketches sets a timeout on `WiFiClientSecure` (`main_node.ino:199`, `eink_node.ino:364`, `fridge_node.ino:135`). In core 3.3.12, `NetworkClientSecure` defaults to a 30 s TCP connect timeout (`_timeout = 30000`) and a 120 s TLS handshake timeout. The MQTT retry is "non-blocking" only between attempts (#120); each attempt itself blocks. With WiFi up but the internet or the VPS down, packets are dropped, not refused, so every attempt waits the full 30 s, then 5 s pause, and again. `loop()` is blocked about 85 % of the time, and `ArduinoOTA.handle()` too, so an OTA flash during such an outage mostly fails. #388 skips MQTT only when *WiFi* is down. | `espClient.setConnectionTimeout(5000)` and `espClient.setHandshakeTimeout(10)` (seconds) next to `setCACert()`, or one `hpSetupTls(client)` in the library for all three. |
| L2 | **A dead fridge sensor shows up as "Node offline".** On a read error the fridge node publishes nothing (`fridge_node.ino:65`, and the 85 °C skip). So a disconnected DS18B20 stops all its messages: after 5 min the backend pushes "Node offline", and the app shows the node offline, though its WiFi and MQTT are fine. The silent-sensor check (#224) would be the right push, but it never sees a row. And if it did, the push would say "no BME280 readings": the sensor name is fixed per sensor kind, not per device (`nodes.py:95`). | Firmware: on a read error, publish the usual message without `temp_c` (plus e.g. `"sensor_error": true`), so the node stays online and `check_sensors` reports the silent sensor after 15 min. Backend: the sensor's name from `config.DEVICES` (e.g. `"sensor": "DS18B20"` for the fridge). Contract: `temp_c` becomes optional on `fridge_temp` (`mqtt.md` §fridge). |
| L3 | **"Window still open?" can push again and again.** `window_open_on_leave()` (`air.py:174`) pushes on every location arm while a window spell is on, with no once-per-spell guard (`nudge_close` has `air:window_nudged`). Two cases repeat it: GPS jitter at the 250 m geofence edge (exit, enter, exit), or `ArmWorker` retrying an arm whose response was lost after the server had processed it. | Remember the spell it was sent for (`air:window_left = <spell start>`) and push once per spell. |
| L4 | **Two certificate targets on the same port would share one alert.** The watchdog key is `cert_<port>` (`server_checks.py:131`, `:136`). With two `:443` targets, one being fine ends the other's alert, and their pushes replace each other on the phone. This is latent: today's two targets are on 443 and 8883. | Key it by the whole target (`cert_<host>_<port>`), with a one-off rename of the existing state key or a note that the first check after the change starts fresh. |

## 2. Checked and fine

These edge cases were looked at specifically:

- **Bad payload fields:**
  - JSON `NaN`/`Infinity` (Python's parser accepts them) fail the range checks and are dropped.
  - `read_int` turns them into None.
  - A non-object payload or a missing device is logged and dropped; nothing escapes `on_message`.
- **Retained messages** on a backend reconnect are skipped, so there are no duplicate rows and no re-sent alerts.
- **Quiet hours:** a window that crosses midnight works; `start == end` means never quiet.
- **Excursions:**
  - Band changes (humidity high → low) are a new excursion.
  - A muted start ends without a push.
  - Failed pushes back off (60 s → 10 min) and aren't saved, so they're retried.
- **Node offline / back:**
  - Under `NODE_LOCK`.
  - A node back before quiet hours end gets no push at all.
  - Flapping is held to one push per 15 min by the cooldown.
  - The offline state survives a backend restart.
- **Roll-up and retention:**
  - At start the roll-up runs before prune, so a 2+ day outage still loses no hours.
  - The last hour is redone.
  - The fridge never mixes into room hours.
- **Air:**
  - With the node down there's no verdict.
  - A spell without a close ends after 3 h.
  - A CO₂ excursion that outlives a node outage ends with "Air is fresh again" once data is back.
  - A failed "Time to air out" is retried after 15 min.
- **Baseline:**
  - A skipped hour restarts the count.
  - A catch-up after downtime isn't checked.
  - DST uses the local hour.
- **API:**
  - Every POST is validated before anything is written.
  - `?days` is ASCII digits only.
  - Notification history shows only sent / muted pushes.
  - Metric labels are escaped.
  - `/api/health` counts from startup until the first message.
- **Fridge alerts:**
  - The node-reboot hold (#300).
  - Urgent through quiet hours (#351), with the "back to normal" that replaces it.
  - Cooldown per state.
  - A glitch row (temp null) still carries the node's `alert`.

## 3. Feature ideas

- **E-ink: keep the last departures through a failed fetch.** One failed BKK request blanks the
  list ("BKK unavailable") for a minute. Showing the last good list for a few minutes, counted
  down from its fetch time, would hide the blips.
- **Fridge on the widget:** still open from this morning's list.

## 4. Suggested order

1. **M1:** your decision first. Then a small backend change plus contract and app text.
2. **L1, L2:** firmware together, so one flash. L2 also needs the backend's sensor name and the
   contract.
3. **L3, L4:** backend, small.
