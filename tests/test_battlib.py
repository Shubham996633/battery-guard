#!/usr/bin/env python3
"""Tests for battlib. No dependencies: python3 tests/test_battlib.py

Covers the cap-detection rule, which is the part most likely to be wrong on
hardware other than the author's, and the duration formatting.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
import battlib  # noqa: E402

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
        FAILED.append(name)


def snap(percent, target_max=85, holding=False, ac_online=False):
    return {"percent": percent, "target_max": target_max,
            "holding": holding, "ac_online": ac_online}


print("cap_reached")
# The regression this function exists for: an ASUS EC given an 85% limit stops
# at 84%, so percent never reaches the target and the old test never fired.
check("stops 1 under target, on AC, not charging",
      battlib.cap_reached(snap(84, holding=True, ac_online=True)), True)
check("exactly at target while charging",
      battlib.cap_reached(snap(85)), True)
check("above target",
      battlib.cap_reached(snap(92)), True)
check("at the edge of the slack window",
      battlib.cap_reached(snap(82, holding=True, ac_online=True)), True)
check("just outside the slack window",
      battlib.cap_reached(snap(81, holding=True, ac_online=True)), False)
# Still climbing: charging has not stopped, so the cap has not been reached.
check("mid-charge, well below target",
      battlib.cap_reached(snap(70)), False)
# Discharging near the target must not look like a finished charge, or the
# notification would fire every time you unplug at a high percentage.
check("discharging near target is not 'reached'",
      battlib.cap_reached(snap(84, holding=False, ac_online=False)), False)
check("stopped but on battery, not AC",
      battlib.cap_reached(snap(84, holding=True, ac_online=False)), False)
check("custom target of 60 stopping at 59",
      battlib.cap_reached(snap(59, target_max=60, holding=True, ac_online=True)), True)
check("explicit slack of 0 rejects undershoot",
      battlib.cap_reached(snap(84, holding=True, ac_online=True), slack=0), False)
check("missing percent does not crash",
      battlib.cap_reached({"target_max": 85}), False)

print("fmt_duration")
check("none", battlib.fmt_duration(None), "--")
check("sub-minute", battlib.fmt_duration(30), "<1m")
check("minutes", battlib.fmt_duration(45 * 60), "45m")
check("hours and minutes", battlib.fmt_duration(4 * 3600 + 31 * 60), "4h 31m")
check("zero-padded minutes", battlib.fmt_duration(2 * 3600 + 5 * 60), "2h 05m")

print("fmt_clock")
check("none", battlib.fmt_clock(None), "--:--")
check("hours and minutes", battlib.fmt_clock(8 * 3600 + 33 * 60), "8:33")
check("under an hour", battlib.fmt_clock(7 * 60), "0:07")

print("load_config")
cfg = battlib.load_config()
check("low_steps sorted descending", cfg["low_steps"], sorted(cfg["low_steps"], reverse=True))
check("target_max is an int", isinstance(cfg["target_max"], int), True)

print("snapshot (this machine)")
s = battlib.snapshot()
if s.get("present"):
    check("percent in range", 0 <= (s["percent"] or 0) <= 100, True)
    check("exactly one of charging/discharging/holding, or none",
          sum(bool(s[k]) for k in ("charging", "discharging", "holding")) <= 1, True)
    check("health is a sane percentage",
          s["health_pct"] is None or 0 < s["health_pct"] <= 120, True)
else:
    print("  skip  no battery on this machine")

print()
if FAILED:
    print(f"{len(FAILED)} test(s) failed: {', '.join(FAILED)}")
    sys.exit(1)
print("all tests passed")
