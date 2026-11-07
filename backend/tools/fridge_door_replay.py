"""Replay the stored fridge readings through the door rule (#336, fridge.door_step) and print
when it would have pushed "Fridge door open?" / "Fridge door closed", plus every fast rise it
watched. For tuning the FRIDGE_DOOR_* limits in config.py against real door openings: raw
readings are kept 2 days (RAW_RETENTION_DAYS).

    sudo -u iot venv/bin/python tools/fridge_door_replay.py [--rise 2.0] [--rise-minutes 5]
        [--open-minutes 10] [--closed 1.0]

The options override the config values for this run only. Read-only; never pushes.
"""

import argparse
from datetime import timedelta

from _common import connect_ro
import config
import fridge
from store import parse_ts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rise", type=float, default=config.FRIDGE_DOOR_RISE_C)
    ap.add_argument("--rise-minutes", type=float, default=config.FRIDGE_DOOR_RISE_MINUTES)
    ap.add_argument("--open-minutes", type=float, default=config.FRIDGE_DOOR_OPEN_MINUTES)
    ap.add_argument("--closed", type=float, default=config.FRIDGE_DOOR_CLOSED_C)
    args = ap.parse_args()
    # door_step reads them from its module
    fridge.FRIDGE_DOOR_RISE_C, fridge.FRIDGE_DOOR_RISE_MINUTES = args.rise, args.rise_minutes
    fridge.FRIDGE_DOOR_OPEN_MINUTES, fridge.FRIDGE_DOOR_CLOSED_C = args.open_minutes, args.closed

    conn = connect_ro()
    rows = [(parse_ts(r["ts"]), r["temp"]) for r in conn.execute(
        "SELECT ts, temp FROM readings WHERE device = ? AND temp IS NOT NULL ORDER BY ts",
        (config.FRIDGE_DEVICE,))]
    conn.close()
    if not rows:
        print("No fridge readings stored.")
        return
    print(f"{len(rows)} fridge readings, {rows[0][0]} .. {rows[-1][0]} UTC; rise {args.rise} °C in "
          f"{args.rise_minutes} min, open after {args.open_minutes} min, closed within {args.closed} °C")

    keep = timedelta(minutes=args.rise_minutes + 1)
    rise, pushed, rises, opens = None, False, 0, 0
    lo = 0
    for i, (ts, temp) in enumerate(rows):
        while rows[lo][0] < ts - keep:
            lo += 1
        before = rise
        rise, event = fridge.door_step(rise, rows[lo:i + 1])
        if rise and not before:
            rises += 1
            print(f"  {ts}  rise from {rise[1]:.1f} to {temp:.1f} °C (since {rise[0]})")
        if event == "open" and not pushed:
            pushed, opens = True, opens + 1
            print(f"  {ts}  PUSH  Fridge door open?  ({temp:.1f} °C)")
        elif event == "closed":
            print(f"  {ts}  {'PUSH  Fridge door closed' if pushed else 'cooled back, no push'}  ({temp:.1f} °C)")
            pushed = False
    print(f"{rises} fast rises, {opens} door-open pushes")


if __name__ == "__main__":
    main()
