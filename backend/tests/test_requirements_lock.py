"""requirements.lock is compiled from requirements.txt and is what CI and the server install:
the two must agree, and every locked package needs a pinned version and a hash.
"""
import re

from support import BACKEND, check


def pins(path):
    """{normalised name: version} of the `name==version` lines in a requirements file."""
    found = {}
    for line in path.read_text().splitlines():
        m = re.match(r"([A-Za-z0-9_.-]+)==([^\s\\;]+)", line)
        if m:
            found[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return found


def test_requirements_lock():
    wanted = pins(BACKEND / "requirements.txt")
    lock_text = (BACKEND / "requirements.lock").read_text()
    locked = pins(BACKEND / "requirements.lock")
    check("requirements.txt has pins", len(wanted) >= 6)
    stale = {name: (version, locked.get(name)) for name, version in wanted.items() if locked.get(name) != version}
    check(f"requirements.lock is out of date, compile it again (README, 'Python packages'): {stale}", not stale)
    entries = [e for e in re.split(r"\n(?=[A-Za-z0-9])", lock_text) if not e.startswith("#")]
    check("every locked package has a hash", all("--hash=sha256:" in e for e in entries))
    check("every locked package is pinned", len(entries) == len(locked))
