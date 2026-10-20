# API Validation & Testing

## Overview

These scripts validate the IoT backend API calculations by reimplementing key functions from their documented contracts (docstrings) and comparing against the live API.

**Why this matters:** A test suite and implementation can share assumptions—when both are wrong in the same way, tests will still pass. These validation scripts catch bugs by computing answers using a completely different code path.

## Scripts

### 1. `validate_api.py` — Core Calculation Validation

Reimplements the core API calculations directly from their docstrings and contracts:

- `occupancy_stats(24)` — 24-hour occupancy statistics
- `calculate_combined_confidence(15)` — radar confidence over 15-minute window  
- `get_combined_occupancy()` — current combined occupancy state
- `pressure_3h_change` — barometric pressure trend over 3 hours
- `hourly_rollup` — hourly aggregation structure

**Usage:**
```bash
cd /opt/iot
python3 validate_api.py
```

**Expected output:**
```
✅ All 7 checks PASSED
```

**What it validates:**
- Reimplemented calculations match the live API results
- No implicit assumptions or bugs shared between implementation and tests
- Each calculation works correctly on production data

### 2. `test_api_regressions.py` — Regression & Edge Case Testing

Tests for boundary conditions, edge cases, and common failure modes:

- Value ranges (occupancy 0-100%, confidence 0-100, etc.)
- Temporal consistency (data freshness, time zone boundaries)
- Logical constraints (min ≤ avg ≤ max for temperatures)
- Realistic value bounds (pressure changes, occupancy patterns)

**Usage:**
```bash
cd /opt/iot
python3 test_api_regressions.py
```

**Expected output:**
```
Results: 18 passed, 0 failed, 0 warnings
```

**What it catches:**
- Off-by-one errors in aggregation windows
- Sign errors in calculations (e.g., pressure trend backwards)
- Missing data handling (None vs 0)
- Time zone or daylight-saving bugs

## How to Use

### One-off validation
```bash
# Run core validation
python3 validate_api.py

# Run regression tests
python3 test_api_regressions.py
```

### Automated validation (CI/CD)
```bash
# In your CI pipeline
cd /opt/iot
python3 validate_api.py || exit 1
python3 test_api_regressions.py || exit 1
```

### Continuous monitoring
```bash
# Run periodically to catch regressions
while true; do
  python3 validate_api.py
  sleep 3600  # once per hour
done
```

## What Gets Validated

### From `/api/stats24h_combined`
- `avg_occupancy`: average occupancy % over 24 hours
- `occupied_hours`: hours the space was occupied
- `coverage_hours`: total hours of radar data (should be ~24)

### From `/api/latest`
- `confidence`: max per-device occupancy confidence over 15 minutes
- `occupied`: combined occupancy state (0/1/None)

### From `/api/air`
- `pressure_3h_change`: pressure trend over last 3 hours (hPa)

### From `/api/hourly?days=1`
- Hourly structure: temp, humidity, CO₂, pressure, occupancy aggregates
- Logical consistency: min ≤ avg ≤ max for each metric

## Implementation Notes

Both validation scripts are **intentionally independent** of the source code:

1. **Reimplemented from docstrings only** — calculations are written from the contract/spec in docstrings, not copied from `store.py`
2. **Direct SQLite queries** — loads raw tables (presence_readings, readings, hourly) and recomputes
3. **Pure Python** — uses only standard library + requests module (no pandas, numpy, or frameworks)

This ensures that if the implementation and tests have a shared bug, the reimplementation will compute the same wrong answer independently.

### Example: `occupancy_stats(24)`

**Docstring contract:**
> Occupancy over a window, using each radar's real sample spacing.

**Implementation logic:**
1. Filter presence_readings to last 24 hours
2. Group by device
3. For each device: calculate `occupied % = occupied_count / total_count`
4. For each device: calculate real span = max(ts) - min(ts) in hours
5. Pick device with highest occupancy %, break ties by longer span
6. Return: occupancy %, occupied_hours = span × fraction, coverage_hours = span

This independent reimplementation will catch:
- Wrong time window bounds
- Incorrect grouping logic
- Tie-breaking bugs
- Rounding errors

## Interpreting Results

### All PASSED ✅
The implementation matches the specification exactly. Safe to ship.

### Some FAILED ✗
Disagreement between implementation and contract. Options:
1. **Bug in implementation** — fix the code in `store.py`
2. **Bug in docstring** — the contract is outdated or unclear
3. **Ambiguous spec** — clarify the docstring and both implementations

### Warnings ⚠️
Something unusual but not necessarily wrong:
- Data older than 5 minutes (slow ingestion?)
- Pressure changes >10 hPa/3h (unusual weather, sensor error?)

Investigate to ensure it's not masking a real issue.

## Adding New Validations

To validate a new calculation:

1. **Identify the contract** — read the function's docstring in `store.py` or `api.py`
2. **Reimplement** — write independent code in `validate_api.py` that matches the spec
3. **Fetch API data** — call the endpoint using `api_get()`
4. **Compare** — use `validator.check()` to compare values

Example:
```python
def test_new_stat():
    reimp = my_calculation(presence)
    api = api_get("my_endpoint", auth)
    validator.check("my_stat", reimp, api.get("result"), precision=1)
```

## Troubleshooting

### "API unreachable at http://localhost:5000/api"
- Is the backend running? Check: `ps aux | grep server.py`
- Start it: `cd /opt/iot && python3 server.py`
- Check config: `BIND_HOST` and `PORT` in `config.py`

### "DASH_USER/DASH_PASS not found in .env"
- Credentials are required for API authentication
- Check `.env` file exists and has: `DASH_USER=...` and `DASH_PASS=...`

### Validation fails intermittently
- Recent data (< 2 min old) can affect results
- Presence readings update every ~10 seconds, so results change constantly
- Rerun to see if it stabilizes
- If a check consistently fails, that's the real bug to investigate

## Design Philosophy

**"Compute the same answer a second way."**

The fundamental principle: when code and tests share assumptions, they'll be wrong together. By reimplementing from the spec independently, we:

1. Catch shared assumptions and bugs
2. Verify the spec is actually implemented
3. Identify ambiguities in the contract (docstrings)
4. Create a reference implementation for future maintainers

This approach scales: add new validations as you add features, and the test becomes a living spec of what the API actually does.
