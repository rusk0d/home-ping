# Database schema contract (DRAFT)

Status: draft, reverse-engineered from `backend/server.py` on branch `contracts-draft` (2026-10-13). The live
`data.db` wasn't inspected. Anything marked **UNCERTAIN** is inferred.

## General

- Engine: SQLite, file `data.db` in the backend's working directory (`DB = "data.db"`, relative path).
- Schema owner: **`server.py` `init_db()`**, which runs at import on every start.
  It uses `CREATE TABLE IF NOT EXISTS`, plus `ALTER TABLE readings ADD COLUMN`
  for every `readings` column except `id`/`ts`/`temp`/`humidity` (errors ignored
  if they already exist), so older databases are upgraded on start.
- There is no separate schema script. The old `backend/init_db.py` created an
  incompatible `readings` table and was removed (issue #4).
- No indexes, foreign keys, migrations table or schema version.
- Every query opens and closes its own `sqlite3.connect(DB)`. Connections are short-lived.
- Timestamps: every insert writes `ts` with `datetime('now','localtime')`, a
  TEXT value `"YYYY-MM-DD HH:MM:SS"` in **server local time** (no timezone).
  Columns are declared `TIMESTAMP`/`DATETIME`, which SQLite stores as TEXT.
  Range queries compare these strings to `datetime('now','localtime', '-N ...')`,
  which works because the format is the same. DST changes will cause gaps and overlaps
  (**UNCERTAIN**: impact not assessed).
- Booleans are stored as INTEGER 0/1.

## Tables

### `readings` (environmental data)

Written by: MQTT env handler (`log_to_db`) for every message that has no
`presence` and no `moisture`+`raw` key (currently `home/esp32/bme280` and `home/esp32/co2`).

| Column | Type | Null | Source / meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | payload `device` (default `"esp32"`) |
| `ts` | TIMESTAMP (TEXT) | yes | receive time, local |
| `temp` | REAL | yes | payload `temp_c`, °C |
| `humidity` | REAL | yes | payload `humidity`, %RH |
| `uptime` | INTEGER | yes | payload `uptime`, s (default 0) |
| `occupied` | INTEGER | yes | **always 0** (hard-coded, legacy) |
| `occupancy_confidence` | INTEGER | yes | **always 0** (hard-coded, legacy) |
| `dark` | INTEGER | yes | payload `dark` truthy → 1, else 0. **Always 0 today**, no firmware sends it |
| `co2` | INTEGER | yes | payload `co2`, ppm. NULL when the co2 node omits it (invalid reading) |
| `pressure` | REAL | yes | payload `pressure`, hPa |

Retention: rows older than 8 days are deleted on every insert.

Typical rows: main_node writes `temp`/`humidity`/`pressure` every 30 s. co2_node
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
Volume: ~17,280 rows/day per radar node at the 5 s interval. With 2 nodes and 8 days that's ~276k rows,
with no index on `device`/`ts` (**UNCERTAIN**: query performance not measured).

### `soil_readings`

Written by: MQTT soil handler (`handle_soil_data`) for messages with both `moisture` and `raw`.

| Column | Type | Null | Source / meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | payload `device` (default `"soil_sensor_01"`) |
| `ts` | TIMESTAMP (TEXT) | yes | receive time, local |
| `moisture` | INTEGER | yes | payload `moisture`, % (**UNCERTAIN** unit) |
| `raw_value` | INTEGER | yes | payload **`raw`** (renamed in the DB) |

Retention: last 1000 rows **per device** are kept (no time limit).

### `devices` (heartbeat)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `device` | TEXT PK | no | device ID |
| `last_seen` | TIMESTAMP (TEXT) | yes | last time any message touched it, local |

Upserted by `touch_device()` on every env and presence message and inline in the soil
handler. Sensor nodes only: `init_db()` deletes the `"unknown"` row that
`POST /api/register_token` used to create (#15). Otherwise never deleted.

### `system_state` (key/value settings)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `key` | TEXT PK | no | |
| `value` | TEXT | yes | |

Known keys:

| Key | Values | Seeded | Written by | Read by |
|---|---|---|---|---|
| `occupancy_armed` | `'0'` / `'1'` | `'0'` via `INSERT OR IGNORE` at startup | `POST /api/occupancy_arm` | presence handler (arrival alert), `/api/presence/latest`, `/api/widget` |

### `events` (audit / event log)

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | no | |
| `device` | TEXT | yes | device ID, or `"system"` |
| `ts` | TIMESTAMP (TEXT) | yes | local |
| `type` | TEXT | yes | see below |
| `value` | TEXT | yes | free text |

Retention: **never pruned**.

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
| `SOIL_DRY_ALERT` | soil ID | `sent, moisture=22%` | soil push attempted |

For the push events above, the value starts with `sent, ` or `failed, ` (since #12).
| `OCCUPANCY_ARMED` | `system` | `1` / `0` | `POST /api/occupancy_arm` |

Read by: `/api/restarts` (`RESTART` only), `/api/widget` (last 5 for a device + `home_ping_esp32`).

## Queries by table (from `server.py`)

| Table | Inserts / updates | Reads |
|---|---|---|
| `readings` | `log_to_db` (+ 8-day prune) | `/api/latest`, `/api/latest_all`, `/api/latest2`, `/api/history`, `/api/history_combined` (UNION with presence), `/api/stats24h`, `/api/stats24h_combined`, `/api/hourly`, `/api/air`, `/api/widget` |
| `presence_readings` | `log_presence` (+ 8-day prune) | `occupancy_stats`, `calculate_confidence`, `get_combined_occupancy`, `calculate_combined_confidence`, `get_last_occupancy`, `get_latest_presence`, `any_radar_quiet_minutes`; endpoints `/api/latest_all`, `/api/presence/history`, `/api/hourly`, `/api/history_combined` |
| `soil_readings` | `handle_soil_data` (+ keep 1000/device) | `/api/soil/latest`, `/api/soil/history`, `/api/soil/stats24h` |
| `devices` | `touch_device`, `handle_soil_data` | `/api/latest`, `/api/latest_all`, `/api/presence/latest`, `/api/devices`, `/api/soil/latest` |
| `system_state` | `init_db` seed, `set_state` | `get_state` |
| `events` | `log_event` | `/api/restarts`, `/api/widget` |

Notable derived values:

- `get_combined_occupancy()`: for each radar, its last 3 rows within 2 min. Occupied if any radar has ≥2 rows with `occupied=1`.
- `calculate_combined_confidence(m)`: `MAX` over radars of `AVG(occupied)` in the last `m` minutes, ×100, truncated to an int.
- `any_radar_quiet_minutes()`: minutes since the newest `occupied=1` row of any radar,
  **excluding the single newest row in the table** (`id < MAX(id)`), else 9999.

## Mismatches and issues

1. ~~**`init_db.py` doesn't match `server.py`.**~~ Fixed in #4: `init_db.py` was
   removed, and `server.py` now adds any missing `readings` columns on start. A
   database created by the old script is repaired automatically. Rows it stored
   before the repair have `device = NULL`, so per-device queries don't show them.
2. **Payload `raw` is stored as `raw_value`** and the API renames it back to `raw`.
3. **Legacy columns that are always written as 0:** `readings.occupied`,
   `readings.occupancy_confidence`, `readings.dark`. All occupancy now lives in
   `presence_readings`.
4. **Retained MQTT messages create duplicate rows** with a fresh `ts` every time
   the backend reconnects to the broker (see `mqtt.md`).
5. **In-memory state that affects data meaning** is not in the DB: `LAST_UPTIME`
   (restart detection baseline), `CO2_SENSOR_OK`, `LAST_NOTIFICATION`,
   `RADAR_FAIL_COUNT`. All of it resets when the backend restarts.
6. **`any_radar_quiet_minutes` excludes the newest row of any device**, not the
   current device's. With two radars interleaving every 5 s, it skips the
   *other* radar's latest row (**UNCERTAIN**: the intent was "exclude the row
   being processed", but the current row is inserted after this query runs, so
   the exclusion is probably unnecessary).
7. **`events` and `devices` grow without limit.**
8. **No indexes** on `(device, ts)` / `(device, id)` for tables queried by those columns every 5 s.
