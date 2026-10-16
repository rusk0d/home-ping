"""Shared by the backend tests (#124)."""
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent   # backend/


def check(label, cond):
    """One named check: fails the test with [label] as the message."""
    assert cond, label
