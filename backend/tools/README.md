# Validation tools

Checks against the **live** database and API, by computing things a different way than
the backend does. They complement the unit tests in `backend/tests` (fixtures, run in CI);
these need the real `data.db` and a running backend, so they don't run in CI.

| Script | Checks | Needs the API |
|---|---|---|
| `validate_db.py` | invariants that must always hold: occupied ≤ coverage hours, hourly rows match the raw rows, raw retention, RESTART events vs. uptimes, `devices.last_seen` | no |
| `validate_db_deep.py` | edge cases: future or backwards timestamps, value bounds, 0→1→0 occupancy spikes, OCCUPANCY_CHANGE events vs. readings, window events alternating, min ≤ avg ≤ max, alert cooldowns | no |
| `validate_api.py` | re-implements occupancy stats, confidence, combined occupancy and the 3 h pressure change from their docstrings and compares with `/api/stats24h_combined`, `/api/latest`, `/api/air` | yes |
| `api_regressions.py` | value ranges, shapes and freshness of the API responses | yes |

Each prints its findings and exits 1 when something failed.

## Running

On the server, from the backend folder, **as the service user**:

```
cd /opt/iot
sudo -u iot venv/bin/python tools/validate_db.py
sudo -u iot venv/bin/python tools/validate_db_deep.py
sudo -u iot venv/bin/python tools/validate_api.py
sudo -u iot venv/bin/python tools/api_regressions.py
```

- They open `data.db` **read-only** and take the API login and settings from `config.py`
  (so `.env`), see `_common.py`.
- They refuse to run as root: opening the WAL database as root can leave
  `data.db-wal`/`data.db-shm` owned by root, and the backend (user `iot`) then can't write.
- Deploy: copy `backend/tools/` to `/opt/iot/tools/` (`chown -R iot:iot`).

A finding is either a bug in the backend or a wrong expectation in the check; both are
worth an issue. Known edge: `validate_api.py` and the API compute "now" a moment apart, so
a value can differ right at a window boundary; run it again before chasing it.
