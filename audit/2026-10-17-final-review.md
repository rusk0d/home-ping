# Final review — 2026-10-17

Scope: the repository at `main` = `8863e87` (after #202), plus read-only checks on the
production server: ports, firewall, services and timers, certificates, file modes, backups,
logs, packages. Nothing was changed. Issues: H1 #205, M1 #206, M2 #122, L1 #207, L2 #208, L3 #209,
L4 #210, L5 #211 (L6 and the info items have none). The earlier reviews are `2026-10-15-code-review.md`,
`2026-10-16-code-review.md` and `2026-10-16-code-review-2.md`. Their code findings are all
done or closed, so this review concentrates on operations and on what changed on
2026-10-16/17 (TLS, OTA, CI split, CSP).

## 0. State

- **Repo:** local `main` = `origin/main`, the working tree is clean, and no PRs are open.
  Backend: 32 tests pass and `ruff` is clean. Both sketches compile with
  `PartitionScheme=min_spiffs` (main_node 56%, eink_node 63%).
- **Open issues:** #19 (radar units on hardware), #80 (widget while the screen is off), #122
  (off-server backups), #167 (CO₂ ABC / temperature offset; reopened and deliberately parked).
- **Server:** everything runs (`iot-backend`, `mosquitto`, `nginx`, `fail2ban`, backup and
  certbot timers). 34% disk used, 513 MB RAM available, `/api/health` ok. `/opt/iot` is `750`;
  `data.db`, `.env`, the Firebase key and the backups are `600`/`700`, owned by `iot`.
  Public ports are 22, 80, 443 and 8883 only. 1883 is localhost only; 5000 and 4999 are on
  127.0.0.1.
- **Nodes:** both are on MQTT over TLS (8883, `sensor`), with OTA. `#192`/`#198` were
  checked on the eink node: tram list OK, lowest free heap 59 KB, presence every 5 s.

## 1. Since review 2: resolved

| Review 2 item | Resolution |
|---|---|
| S1 security headers | #168 (CSP, HSTS, nosniff, `server_tokens off`); CSP fixed for mqtt.js's timer worker in #204 |
| S2 server leftovers | #169: `/root/iot` removed, ufw 5000 removed, 9001 set to localhost (see **L2**) |
| S3 file modes | #169: `UMask=0077` + `chmod` |
| B1 CO₂ offset | #167 parked on purpose (accepted under-reading) |
| B2, B3 | #170 |
| B4, E1 | #172 (`register_token` and the app's compat code removed) |
| B6 eink fetch gap | #171/#192 (fetch on core 0), confirmed on hardware |
| E2 raw retention | #173 (week view from `hourly`, raw kept 2 days, VACUUM: 56 → 17 MB) |
| E3 WAL | #174 |
| E5, E6 docs, ruff | #175 |
| plain MQTT over the internet (#31) | #199: TLS on 8883, 1883 localhost-only |
| USB-only flashing (#79) | #200: ArduinoOTA, Minimal SPIFFS layout |
| #10 shared presence topic | closed, not planned (backend skips retained messages; main-room occupancy on purpose) |
| CI minutes (90% used) | #201: workflows per folder, no rerun on merge |

## 2. Findings

| ID | Sev. | Finding | Suggested fix |
|---|---|---|---|
| H1 | **high** | **Certificate renewal is broken (#205).** `certbot.service` has failed on every run since 2026-09-10 with `AttributeError: module 'lib' has no attribute 'GEN_EMAIL'`. A manual `pip install authlib` on 2026-09-10 installed `cryptography 50.0.1` into `/usr/local/lib/python3.10/dist-packages`, which shadows Ubuntu's `python3-cryptography 3.4.8` that the apt certbot 1.21 (with pyOpenSSL 21) needs. `ruskodavid.hu` expires **2026-12-03** and is used by the web page, `/api/` (the app) and now MQTT TLS (both nodes). Without a fix, everything stops then. `traffic.ruskodavid.hu` (not this repo) already expires **2026-10-24**. | Move certbot to the snap, which is isolated from system pip (snapd 2.76 is installed): `apt remove certbot python3-certbot-nginx`, `snap install --classic certbot`, `ln -s /snap/bin/certbot /usr/bin/certbot`, `certbot renew --dry-run`. The renewal configs and the Mosquitto deploy hook in `/etc/letsencrypt` stay. Longer term, give `/scrapper` its own venv and `pip uninstall` the system-wide packages (see I1). |
| M1 | medium | **Nothing watches certificate expiry (#206).** H1 went unnoticed for 5 weeks. The watchdog (#176) checks backups and disk only. | Add a check to `server_checks.py`: open a TLS connection to the broker host on 8883 (and 443), read `notAfter`, and push "Certificate expires in N days" below 14 days (once per excursion, quiet hours postpone, like the others). It needs no root and no file access. Contract: notifications.md. |
| M2 | medium | **Backups only on the server's disk** (#122). The user plans to copy them home by hand. | Automate the pull from the home PC: a Windows scheduled task with `scp root@…:/opt/iot/backups/data-$(date).db.gz` (keys only, read-only), or `rclone` to a cloud drive from the server. Test a restore once (#78 has the steps). |
| L1 | low | **Reboot pending, 14 packages upgradable (#207).** `/var/run/reboot-required` exists; unattended-upgrades is on with `Automatic-Reboot "false"`, so kernel and library fixes aren't active (uptime 23 days). | `apt upgrade` and a reboot at a quiet time. Both nodes and the page reconnect by themselves, and the backend is a systemd service. Optionally `Automatic-Reboot "true"` with `Automatic-Reboot-Time "04:00"`. |
| L2 | low | **Mosquitto 9001 still listens on all interfaces (#208)** (`*:9001` in `ss`), although `mosquitto.conf` says `listener 9001 127.0.0.1` (#169). Mosquitto 2.0.11 hands websockets listeners to libwebsockets, which ignores that address. ufw blocks 9001, so it isn't reachable from outside. | Check whether `bind_interface lo` under that listener binds it (then `ss -ltn` shows `127.0.0.1:9001`); otherwise rely on ufw and note it in `contracts/mqtt.md`. |
| L3 | low | **Firmware core mismatch (#209):** CI compiles with ESP32 core **3.3.12**, but the Arduino IDE that builds the real (and now OTA) uploads has **3.3.7**. CI can pass on code that the IDE build treats differently, and the other way round. | Update the IDE's core to 3.3.12 (Boards Manager), or pin CI to 3.3.7. Keep them equal from then on (note in README). |
| L4 | low | **Docs drift (#210).** CLAUDE.md "Firmware: compiled in the Arduino IDE (arduino-cli in CI later)" (CI has done so since #76), and it doesn't mention the partition scheme, OTA or TLS. `contracts/mqtt.md` mismatch 1 still describes the shared presence topic as open and points to #10 (closed as not planned). | CLAUDE.md Commands: "Firmware: Arduino IDE, Partition Scheme Minimal SPIFFS; first flash USB, then OTA (network port, `OTA_PASS`)". mqtt.md: reword mismatch 1 as a decision. |
| L5 | low | **Branch clutter (#211):** 19 local and 23 remote merged branches. | `git branch --merged main | grep -v main | xargs git branch -d`, delete the remote ones, and turn on "Automatically delete head branches" in the repo settings. |
| L6 | low | **Mosquitto 2.0.11** (Ubuntu 22.04's package). Upstream 2.0.22 has keepalive and websockets fixes (e.g. "Backport keepalive checking", 2.0.21). Nothing is broken today. | Optional: the Mosquitto PPA, or leave it until the next Ubuntu LTS. |
| I1 | info | **Other projects run as root on the same VPS:** `/scrapper/app.py` (system Python, root, 127.0.0.1:4999) and `/traffic/server.js` (PM2, root, `*:3000`, ufw-blocked). The scrapper's system-wide pip packages caused H1. A hole in either gives root, and with it every Home Ping secret. | A venv and its own user for `/scrapper`, its own user for `/traffic`; `pip` only inside venvs on this server. |
| I2 | info | **OTA** uses ArduinoOTA's MD5 challenge with `OTA_PASS`, and listens on the home LAN only (port 3232). That's fine for a home network; anyone on the WiFi can try passwords. | Keep `OTA_PASS` long and random; if there's a guest WiFi, keep the nodes off it. |
| I3 | info | **CI minutes:** 1,801 of 2,000 used this cycle, before #201. A one-component PR now costs 1–7 minutes, a docs PR nothing, and Dependabot PRs only their own workflow. | Watch the next cycle; if needed, cache the ESP32 core in the firmware workflow (the biggest job, ~4 min). |

## 3. Suggested order

1. **H1 now** (5 min on the server). It also saves `traffic.ruskodavid.hu`, which expires in 7 days.
2. **L1** reboot with the updates, at a quiet time.
3. **M1** cert-expiry watchdog (backend PR), so this can't go unnoticed again.
4. **M2** automated off-server backups (#122).
5. **L3, L4, L5** small cleanups in one sitting; **L2** when convenient.
6. Open issues as before: #19 (hardware check), #80 (widget), #167 parked.
