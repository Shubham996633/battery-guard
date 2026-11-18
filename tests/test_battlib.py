#!/usr/bin/env python3
"""Tests for battlib. No dependencies: python3 tests/test_battlib.py

Covers the cap-detection rule, which is the part most likely to be wrong on
hardware other than the author's, and the duration formatting.
"""

import os
import pathlib
import sys
import tempfile

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

print("reminder_due")
check("nothing fired yet", battlib.reminder_due(None, 1000, 5), False)
check("too soon", battlib.reminder_due(1000, 1299, 5), False)
check("exactly on time", battlib.reminder_due(1000, 1300, 5), True)
check("0 minutes means never repeat", battlib.reminder_due(1000, 99999, 0), False)

print("health history")
with tempfile.TemporaryDirectory() as folder:
    history = pathlib.Path(folder) / "nested" / "health.csv"
    check("empty when there is no file", battlib.read_health_history(history), [])
    check("first reading of the day is saved",
          battlib.record_health({"health_pct": 83.64}, "2026-11-07", history), True)
    check("second reading that day is not",
          battlib.record_health({"health_pct": 83.0}, "2026-11-07", history), False)
    check("next day is saved",
          battlib.record_health({"health_pct": 83.2}, "2026-11-08", history), True)
    check("firmware without health data saves nothing",
          battlib.record_health({"health_pct": None}, "2026-11-09", history), False)
    check("reads oldest first",
          battlib.read_health_history(history), [("2026-11-07", 83.6), ("2026-11-08", 83.2)])
    with history.open("a") as handle:
        handle.write("garbage\n2026-11-09,not-a-number\n")
    check("bad lines are skipped", len(battlib.read_health_history(history)), 2)

print("sparkline")
check("empty", battlib.sparkline([]), "")
check("rises", battlib.sparkline([1, 2, 3]), "▁▅█")
check("flat does not crash", battlib.sparkline([80, 80]), "▁▁")

print("busiest_apps")
ticks = battlib.CLOCK_TICKS_PER_SECOND
before = {1: ("firefox", 0), 2: ("firefox", 0), 3: ("code", 0), 4: ("idle", 7)}
after = {1: ("firefox", ticks), 2: ("firefox", ticks), 3: ("code", ticks // 2), 4: ("idle", 7)}
check("adds same-named processes and sorts",
      battlib.busiest_apps(before, after, 2), [("firefox", 100.0), ("code", 25.0)])
check("a counter going backwards is not negative",
      battlib.busiest_apps({1: ("a", 50)}, {1: ("a", 10)}, 1), [])
check("a reused pid counts only the new program",
      battlib.busiest_apps({1: ("old", 500)}, {1: ("new", ticks)}, 1), [("new", 100.0)])
check("top limits the list",
      len(battlib.busiest_apps({}, {i: (f"p{i}", 10) for i in range(9)}, 1, top=3)), 3)
with tempfile.TemporaryDirectory() as folder:
    for pid, stat in (("10", "10 (my (odd) app) S " + "0 " * 10 + "7 3 " + "0 " * 8 + "5 0"),
                      ("11", "11 (kworker/0) S " + "0 " * 10 + "9 9 " + "0 " * 8 + "0 0"),
                      ("12", "12 (cut")):
        os.mkdir(f"{folder}/{pid}")
        pathlib.Path(f"{folder}/{pid}/stat").write_text(stat)
    os.mkdir(f"{folder}/notapid")
    check("parses odd names, skips kernel threads and bad data",
          battlib.read_cpu_ticks(folder), {10: ("my (odd) app", 10)})

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
