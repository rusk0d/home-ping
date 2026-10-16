"""Runs each script in cases/ in its own Python process (#76).

server.py connects to SQLite, MQTT and Firebase and starts threads at import time
(#73), so every case stubs those libraries, imports server.py fresh against a temp
DB and prints "PASS <label>" / "FAIL <label>" per check. A case fails on any FAIL
line, a non-zero exit, or no PASS line at all.

Run: `pytest backend/tests` (or `python backend/tests/cases/<case>.py backend` for one).
"""
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
CASES = sorted((TESTS / "cases").glob("*.py"))


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_case(case):
    r = subprocess.run([sys.executable, str(case), str(BACKEND)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    lines = r.stdout.splitlines()
    failed = [l for l in lines if l.startswith("FAIL ")]
    passed = [l for l in lines if l.startswith("PASS ")]
    assert r.returncode == 0, f"exit {r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    assert not failed, "\n".join(failed)
    assert passed, f"no checks ran\n{r.stdout[-2000:]}"
