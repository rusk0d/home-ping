# Database schema contract

The backend's SQLite database (`data.db`); schema and migrations in `backend/store.py`.
It is the source of truth: update it in the same change as the code (see CLAUDE.md).
First written on 2026-10-13 by reading the code; anything still marked **UNCERTAIN** was
inferred then and hasn't been confirmed since.

## General

- Engine: SQLite, file `data.db` in the backend's working directory (`DB = "data.db"`, relative path).
- Schema owner: **`store.py` `init_db()`** (backend), which runs on every start (`server.prepare()`, called by `start()`; since #73 importing the modules does nothing). "`server.py`" below means the backend as a whole.
  It uses `CREATE TABLE IF NOT EXISTS`, plus `ALTER TABLE readings ADD COLUMN`
  for every `readings` column except `id`/`ts`/`temp`/`humidity` (errors ignored
  if they already exist), so older databases are upgraded on start.
- There is no separate schema script. The old `backend/init_db.py` created an
  incompatible `readings` table and was removed (issue #4).
- Indexes (since #18, created by `init_db()`, which then runs `ANALYZE`):
  `presence_readings(device, id)`, `presence_readings(device, ts)`, `readings(device, id)`,
  `readings(device, ts)`, `events(type, ts)`. Deliberately **no** plain `ts` index: it made
  SQLite choose worse plans. Without `ANALYZE` statistics it also picks the wrong index.
- No foreign keys, migrations table or schema version.
- Every query opens and closes its own `sqlite3.connect(DB)`. Connections are short-lived.
  Kept on purpose (#18): with the indexes, a whole MQTT message takes ~10 ms.
- Timestamps: every insert writes `ts` / `last_seen` with `datetime('now')`, a TEXT value
  `"YYYY-MM-DD HH:MM:SS"` in **UTC** (since #65). Columns are declared `TIMESTAMP`, which
  SQLite stores as TEXT. Range queries compare with `datetime('now', '-N ...')`, so
  windows and the offline check are unaffected by DST changes.
  "Today" queries use `datetime('now','localtime','start of day','utc')` (Budapest
  midnight as UTC) and label hours with `strftime(..., ts, 'localtime')`.
- Migration (#65): until then timestamps were server-local. `init_db()` converts the
  existing rows of `readings`, `presence_readings`, `events` and `devices` once with
  `datetime(ts, 'utc')` (each row's own DST offset) and marks it in `system_state`
  (`ts_utc` = `'1'`).
- Booleans are stored as INTEGER 0/1.

## Tables

### `readings` (environmental data)

Written by: MQTT env handler (`log_to_db`) for every message that has no
`presence` key (currently `home/esp32/bme280` and `home/esp32/co2`).

| Column | Type | Null | Source / meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | payload `device` (default `"esp32"`) |
| `ts` | TIMESTAMP (TEXT) | yes | receive time, local |
| `temp` | REAL | yes | payload `temp_c`, °C |
| `humidity` | REAL | yes | payload `humidity`, %RH |
| `uptime` | INTEGER | yes | payload `uptime`, s (default 0) |
| `co2` | INTEGER | yes | payload `co2`, ppm. NULL when the co2 node omits it (invalid reading) |
| `pressure` | REAL | yes | payload `pressure`, hPa |
| `co2_ok` | INTEGER | yes | payload `co2_ok` as 1/0; NULL when the message has none (added in #14, via the ALTER loop) |

Retention: rows older than 8 days are deleted on every insert.

`occupied`, `occupancy_confidence` and `dark` (always 0) were dropped in #14: `init_db()`
rebuilds the table without them if an older database still has them (works on any SQLite
version; the rows are kept).

Typical rows (since the node merge, #57, all from main_node `home_ping_esp32`; before, the CO₂ rows came from `co2_sensor_esp32`): main_node writes `temp`/`humidity`/`pressure` every 30 s. The CO₂ part
writes `co2` (or NULL) every 60 s with NULL temp/humidity/pressure.

### `presence_readings` (LD2410C radar)

Written by: MQTT presence handler (`log_presence`) for every message with a `presence` key.

| Column | Type | Null | Source / meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | payload `device` (default `"home_ping_esp32"`) |
| `ts` | TIMESTAMP (TEXT) | yes | receive time, local |
| `occupied` | INTEGER 0/1 | yes | **derived by the backend**: `moving OR (stationary AND signal >= 15)` |
| `presence` | INTEGER 0/1 | yes | payload `presence` |
| `stationary` | INTEGER 0/1 | yes | payload `stationary` |
| `moving` | INTEGER 0/1 | yes | payload `moving` |
| `distance` | INTEGER | yes | payload `distance`, cm (**UNCERTAIN** unit) |
| `signal` | INTEGER | yes | payload `signal`, 0–100 energy |
| `rssi` | INTEGER | yes | payload `rssi`, dBm |
| `uptime` | INTEGER | yes | payload `uptime`, s |
| `radar_connected` | INTEGER 0/1 | yes | payload `radar_connected` (default 1) |

Retention: rows older than 8 days are deleted on every insert.
Volume: ~17,280 rows/day per radar node at the 5 s interval. With 2 nodes and 8 days that's ~276k rows
(~45 MB database with the indexes). Measured on such a DB (#18): a presence message ~10 ms,
`/api/latest_all` ~7 ms (265 ms without indexes).

`soil_readings` was removed with the soil feature (#9); `init_db()` drops it if it exists.

### `devices` (heartbeat)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `device` | TEXT PK | no | device ID |
| `last_seen` | TIMESTAMP (TEXT) | yes | last time any message touched it, local |

Upserted by `touch_device()` on every env and presence message. Sensor nodes only: `init_db()` deletes the `"unknown"` row that
`POST /api/register_token` used to create (#15). Retention: rows not seen for
`DEVICES_RETENTION_DAYS` (30) are deleted in `touch_device()`, so a retired node drops off the list.

### `system_state` (key/value settings)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `key` | TEXT PK | no | |
| `value` | TEXT | yes | |

Known keys:

| Key | Values | Seeded | Written by | Read by |
|---|---|---|---|---|
| `occupancy_armed` | `'0'` / `'1'` | `'0'` via `INSERT OR IGNORE` at startup | `POST /api/occupancy_arm` | presence handler (arrival alert), `/api/occupancy_status` |
| `cooldown:<key>` | unix time (float, as text) when the cooldown for alert `<key>` started (e.g. `cooldown:HUMIDITY_home_ping_esp32`) | no | `alert()` via `set_cooldown()` | loaded into `LAST_NOTIFICATION` at startup (#5) |
| `arrival_started` | unix time an arrival started (first occupied reading after the quiet spell), `'0'` once confirmed | no | presence handler | presence handler (arrival alert) |
| `arrival_quiet` | quiet minutes before that arrival, as an int | no | presence handler | arrival alert body |
| `env_alerted:HUMIDITY_<device>` / `env_alerted:TEMP_<device>` | `'high'`/`'low'` while that humidity/temperature excursion has been pushed or muted, `''` after it ended (#117) | no | env handler (`ingest.set_env_alerted`) | env handler |
| `radar_alerted:<device>` | `'1'` while that radar's current outage has been pushed, else `'0'` | no | presence handler via `set_radar_alerted()` | loaded into `RADAR_ALERTED` at startup (#5) |
| `ts_utc` | `'1'` once the timestamps were converted to UTC | no | `init_db()` (#65) | `init_db()` |
| `node_offline:<device>` | `'1'` while that node's current outage has been pushed, `'m'` while it is offline but the push was muted (#82), else `'0'` | no | `check_nodes()` / `node_back_online()` | loaded into `NODE_OFFLINE` at startup (#13) |
| `notify:<kind>` | `'1'` / `'0'` per alert type (`humidity`, `temperature`, `radar`, `nodes`); missing = `'1'` | no | `POST /api/notification_settings` (#82) | `alert()` via `alert_muted()`, `GET /api/notification_settings` |
| `quiet_hours` | `'1'` / `'0'`; missing = `'0'` | no | `POST /api/notification_settings` (#82) | `in_quiet_hours()`, `GET /api/notification_settings` |
| `quiet_start` / `quiet_end` | `"HH:MM"`, server local time; missing = `22:00` / `07:00` | no | `POST /api/notification_settings` (#82) | `in_quiet_hours()`, `GET /api/notification_settings` |

### `events` (audit / event log)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | device ID, or `"system"` |
| `ts` | TIMESTAMP (TEXT) | yes | local |
| `type` | TEXT | yes | see below |
| `value` | TEXT | yes | free text |

Retention: rows older than `EVENTS_RETENTION_DAYS` (30) are deleted in `log_event()`.

Event types written by `server.py`:

| `type` | `device` | `value` example | Written when |
|---|---|---|---|
| `RESTART` | sensor ID | `uptime 86400s -> 12s` | `uptime` dropped by > 120 s vs. last message (in-memory baseline) |
| `OCCUPANCY_CHANGE` | radar ID | `occupied` / `vacant` | derived `occupied` differs from the previous row of that radar |
| `RADAR_DISCONNECTED` | radar ID | `sent, no frames from LD2410C` | radar-offline push attempted |
| `OCCUPANCY_ALERT` | radar ID | `sent, after 75 min quiet` | arrival push attempted |
| `HUMIDITY_HIGH_ALERT` | env ID | `sent, humidity=67.2` | humidity push attempted |
| `HUMIDITY_LOW_ALERT` | env ID | `sent, humidity=23.9` | humidity push attempted |
| `TEMP_ALERT` | env ID | `sent, temp=17.6` | temperature push attempted |
| `STATUS` | node ID | `online` / `offline` | live message on `home/esp32/<device>/status` (#13) |
| `NODE_OFFLINE` | node ID | `sent, last seen 2026-10-16 06:12:40` (UTC) | node-offline push attempted (#13) |
| `NODE_ONLINE` | node ID | `sent, data again` | back-online push attempted (#13); `no push (offline alert was muted), data again` when the offline alert was muted (#82) |
| `OCCUPANCY_ARMED` | `system` | `1` / `0` | `POST /api/occupancy_arm` |
| `NOTIFICATION_SETTINGS` | `system` | `notify:humidity=0, quiet_start=23:00` | `POST /api/notification_settings` changed something (#82) |

For the push events above, the value starts with `sent, ` or `failed, ` (since #12), or
`muted (off), ` / `muted (quiet hours), ` when the alert type is switched off or in quiet hours (#82).

Read by: `/api/restarts` (`RESTART` only).

## Queries by table (from `server.py`)

| Table | Inserts / updates | Reads |
|---|---|---|
| `readings` | `log_to_db` (+ 8-day prune) | `/api/latest`, `/api/latest_all`, `/api/stats24h_combined`, `/api/hourly`, `/api/air` |
| `presence_readings` | `log_presence` (+ 8-day prune) | `occupancy_stats`, `calculate_confidence`, `get_combined_occupancy`, `calculate_combined_confidence`, `get_last_occupancy`, `any_radar_quiet_minutes`; endpoints `/api/latest_all`, `/api/hourly` |
| `devices` | `touch_device` | `/api/latest`, `/api/latest_all`, `/api/devices` |
| `system_state` | `init_db` seed, `set_state` | `get_state` |
| `events` | `log_event` | `/api/restarts` |

Notable derived values:

- `get_combined_occupancy()`: for each radar, its last 3 rows within 2 min. Occupied if any radar has ≥2 rows with `occupied=1`.
- `calculate_combined_confidence(m)`: `MAX` over radars of `AVG(occupied)` in the last `m` minutes, ×100, truncated to an int.
- `any_radar_quiet_minutes()`: minutes since the newest `occupied=1` row of any radar, else 9999.
  It runs before the current message's row is inserted.

## Mismatches and issues

1. **In-memory state:** `LAST_UPTIME` (restart detection baseline) and `RADAR_FAIL_COUNT`
   reset when the backend restarts. That is harmless: the first message after a
   restart only sets the baseline, and an outage needs 12 fresh disconnected messages
   anyway. Cooldowns and outage flags are in `system_state` (#5); `co2_ok` in `readings` (#14).

## Changelog

Fixed mismatches, oldest first (issue number: what changed).

- #4: `init_db.py` removed; the backend adds missing `readings` columns on start. Rows stored by
  the old script before the repair have `device = NULL`, so per-device queries don't show them.
- #5: retained MQTT messages are skipped, so they no longer create duplicate rows.
- #9: soil table dropped (and with it the `raw` → `raw_value` renaming).
- #14: legacy always-0 occupancy columns dropped from `readings`.
- #18: 30-day retention for `events` and `devices`; indexes added (see Conventions).
- Arrival alert fix (#101): `any_radar_quiet_minutes` no longer skips the newest row in the table.
