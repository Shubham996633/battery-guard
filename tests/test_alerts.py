#!/usr/bin/env python3
"""Tests for the alert loop in battery-guardd. No dependencies: python3 tests/test_alerts.py

Feeds the real loop a scripted list of battery readings with a fake clock, and
records what it would notify and play. Nothing is shown and no sound plays.
"""

import importlib.machinery
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import battlib  # noqa: E402

loader = importlib.machinery.SourceFileLoader("guardd", str(ROOT / "bin" / "battery-guardd"))
spec = importlib.util.spec_from_loader("guardd", loader)
guardd = importlib.util.module_from_spec(spec)
loader.exec_module(guardd)

FAILED = []
MINUTE = 60


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
        FAILED.append(name)


class Stop(Exception):
    pass


def reading(percent, state, ac=None, cap_limit=None, target=90):
    return {
        "present": True, "percent": percent, "status": state,
        "charging": state == "Charging", "discharging": state == "Discharging",
        "holding": state == "Full",
        "ac_online": state != "Discharging" if ac is None else ac,
        "cap_limit": cap_limit, "cap_applied": cap_limit == target,
        "target_max": target, "target_min": 20, "seconds_to_empty": 3600,
    }


def run(script, **config):
    """script is [(minutes since start, reading)]. Returns notifications and sounds."""
    settings = dict(battlib.DEFAULTS, target_max=90, **config)
    steps = iter(script)
    clock = {"now": 0}
    notified, sounds = [], []

    def next_snapshot(_cfg):
        try:
            minutes, snap = next(steps)
        except StopIteration:
            raise Stop from None
        clock["now"] = minutes * MINUTE
        return snap

    def fake_notify(channel, title, body, icon, urgency="normal", sound=None):
        notified.append((channel, title, urgency))
        if sound:
            guardd.play(sound)

    guardd.battlib.load_config = lambda: settings
    guardd.battlib.snapshot = next_snapshot
    guardd.battlib.record_health = lambda _snap, _today: False  # keep real history clean
    guardd.notify = fake_notify
    guardd.find_player = lambda: (lambda path: ["true"])
    guardd.os.path.exists = lambda _path: True
    guardd.subprocess.Popen = lambda argv, **_kw: None
    original_play = ORIGINAL_PLAY

    def spy_play(kind):
        if guardd._sound_on:
            sounds.append(kind)
        original_play(kind)

    guardd.play = spy_play
    guardd.time.sleep = lambda _seconds: None
    guardd.time.monotonic = lambda: clock["now"]
    guardd.sys.argv = ["battery-guardd"]
    try:
        guardd.main()
    except Stop:
        pass
    return notified, sounds


ORIGINAL_PLAY = guardd.play

print("unplug reminders with no firmware limit")
alerts, sounds = run([(m, reading(91, "Charging")) for m in (0, 1, 4, 5, 6, 10)])
check("repeats at 0, 5 and 10 minutes", len(alerts), 3)
check("is urgent", alerts[0][2], "critical")
check("plays the warning sound each time", sounds, ["warning"] * 3)

alerts, _ = run([(0, reading(91, "Charging")), (5, reading(91, "Discharging")),
                 (10, reading(91, "Discharging"))])
check("stops once unplugged", len(alerts), 1)

alerts, _ = run([(m, reading(91, "Charging")) for m in (0, 5, 10)], reminder_minutes=0)
check("reminder_minutes 0 alerts only once", len(alerts), 1)

alerts, _ = run([(m, reading(80, "Charging")) for m in (0, 5, 10)])
check("no alert below the target", alerts, [])

print("firmware limit working, as on the ASUS")
alerts, sounds = run([(m, reading(90, "Full", cap_limit=90)) for m in (0, 5, 10)])
check("alerts once, no nagging", len(alerts), 1)
check("not urgent", alerts[0][2], "normal")
check("plays the done sound", sounds, ["done"])

print("low battery")
script = [(0, reading(21, "Discharging")), (1, reading(20, "Discharging")),
          (5, reading(16, "Discharging")), (10, reading(15, "Discharging"))]
alerts, _ = run(script)
check("alerts once per step above critical", len(alerts), 2)

alerts, _ = run([(m, reading(9, "Discharging")) for m in (0, 1, 5, 10)])
check("critical repeats after the step alerts", alerts[-1][0], "low")
check("critical alerts at 0 and every 5 minutes",
      len([a for a in alerts if a[2] == "critical"]) >= 3, True)

alerts, _ = run([(0, reading(9, "Discharging")), (5, reading(9, "Charging")),
                 (10, reading(9, "Charging"))])
check("stops once charging", len(alerts), 3)  # the 20, 15 and 10 steps on the first reading

print("charger sounds")
_, sounds = run([(0, reading(50, "Discharging")), (1, reading(50, "Charging")),
                 (2, reading(50, "Discharging"))])
check("plug then unplug, none at startup", sounds, ["plug", "unplug"])
_, sounds = run([(0, reading(50, "Discharging")), (1, reading(50, "Charging"))], plug_sounds=False)
check("plug_sounds false is silent", sounds, [])

print("sound setting")
_, sounds = run([(0, reading(91, "Charging"))], sound=False)
check("sound false silences alerts", sounds, [])

print()
if FAILED:
    print(f"{len(FAILED)} test(s) failed: {', '.join(FAILED)}")
    sys.exit(1)
print("all tests passed")
