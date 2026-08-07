"""Shared battery reader for battery-guard.

Reads only standard sysfs power_supply attributes, so it works on any
laptop; the charge cap additionally needs a kernel driver that exposes
charge_control_end_threshold (asus_wmi, thinkpad_acpi, huawei_wmi, ...).
Developed and verified on Zorin OS 18 / GNOME 46 / ASUS VivoBook.
"""

import json
import os
from pathlib import Path

SYSFS = Path("/sys/class/power_supply")

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "battery-guard"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    # Stop charging here. Written to the EC, so it is enforced in firmware.
    "target_max": 85,
    # "Plug in soon" floor. Used for the drain-to-floor estimate and the
    # first low warning.
    "target_min": 20,
    # Discharge levels that raise a notification, high to low.
    "low_steps": [20, 15, 10, 5],
    # Notify when charging reaches target_max.
    "notify_full": True,
    # Notify if the EC limit drifts away from target_max (e.g. firmware reset).
    "notify_cap_drift": True,
    "poll_seconds": 20,
    "sound": True,
}

# How far below target_max still counts as "the cap stopped charging".
CAP_SLACK = 3


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    cfg["low_steps"] = sorted({int(s) for s in cfg["low_steps"]}, reverse=True)
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _num(path):
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def find_battery():
    for entry in sorted(SYSFS.glob("BAT*")):
        if _read(entry / "present") != "0":
            return entry
    return None


def ac_online():
    for entry in sorted(SYSFS.iterdir()) if SYSFS.exists() else []:
        if _read(entry / "type") in ("Mains", "USB"):
            if _num(entry / "online") == 1:
                return True
    return False


def _energy_wh(bat):
    """Return (now, full, design) in Wh, from energy_* or charge_*+voltage."""
    now = _num(bat / "energy_now")
    full = _num(bat / "energy_full")
    design = _num(bat / "energy_full_design")
    if now is not None and full:
        return now / 1e6, full / 1e6, (design / 1e6 if design else None)

    # Batteries that report charge (µAh) instead of energy (µWh).
    volt = _num(bat / "voltage_now") or _num(bat / "voltage_min_design")
    c_now = _num(bat / "charge_now")
    c_full = _num(bat / "charge_full")
    c_design = _num(bat / "charge_full_design")
    if volt and c_now is not None and c_full:
        v = volt / 1e6
        return (
            c_now / 1e6 * v,
            c_full / 1e6 * v,
            (c_design / 1e6 * v if c_design else None),
        )
    return None, None, None


def _power_w(bat):
    p = _num(bat / "power_now")
    if p is not None:
        return abs(p) / 1e6
    cur = _num(bat / "current_now")
    volt = _num(bat / "voltage_now")
    if cur is not None and volt:
        return abs(cur) / 1e6 * (volt / 1e6)
    return None


def snapshot(cfg=None):
    """One coherent reading of battery state plus derived estimates."""
    cfg = cfg or load_config()
    bat = find_battery()
    if bat is None:
        return {"present": False, "config": cfg}

    percent = _num(bat / "capacity")
    status = _read(bat / "status") or "Unknown"
    now_wh, full_wh, design_wh = _energy_wh(bat)
    power = _power_w(bat)
    cycles = _num(bat / "cycle_count")
    cap_limit = _num(bat / "charge_control_end_threshold")

    target_max = int(cfg["target_max"])
    target_min = int(cfg["target_min"])

    health = None
    if full_wh and design_wh:
        health = full_wh / design_wh * 100.0

    charging = status == "Charging"
    discharging = status == "Discharging"
    on_ac = ac_online()
    # Charging finished or was stopped by the EC. Firmware disagrees on how to
    # say it: "Not charging", "Full", or just "Unknown" while sitting on AC.
    holding = status in ("Not charging", "Full") or (status == "Unknown" and on_ac)

    to_target = to_full = to_empty = to_floor = None
    if power and power > 0.05 and full_wh and now_wh is not None:
        if charging:
            tgt_wh = full_wh * target_max / 100.0
            if now_wh < tgt_wh:
                to_target = (tgt_wh - now_wh) / power * 3600
            if now_wh < full_wh:
                to_full = (full_wh - now_wh) / power * 3600
        elif discharging:
            to_empty = now_wh / power * 3600
            floor_wh = full_wh * target_min / 100.0
            if now_wh > floor_wh:
                to_floor = (now_wh - floor_wh) / power * 3600

    return {
        "present": True,
        "path": str(bat),
        "percent": percent,
        "status": status,
        "charging": charging,
        "discharging": discharging,
        "holding": holding,
        "ac_online": on_ac,
        "power_w": power,
        "energy_now_wh": now_wh,
        "energy_full_wh": full_wh,
        "energy_design_wh": design_wh,
        "health_pct": health,
        "cycle_count": cycles,
        "cap_limit": cap_limit,
        "cap_applied": cap_limit == target_max,
        "target_max": target_max,
        "target_min": target_min,
        "seconds_to_target": to_target,
        "seconds_to_full": to_full,
        "seconds_to_empty": to_empty,
        "seconds_to_floor": to_floor,
        "config": cfg,
    }


def cap_reached(snap, slack=None):
    """True once charging has effectively hit the cap.

    Embedded controllers routinely terminate a point or two below the setpoint
    - an 85% limit stops at 84% on ASUS - so a plain `percent >= target_max`
    test never fires on that hardware. Charging having *stopped* while on AC
    near the target is the reliable signal.
    """
    slack = CAP_SLACK if slack is None else slack
    percent = snap.get("percent") or 0
    target = snap.get("target_max") or DEFAULTS["target_max"]
    if percent >= target:
        return True
    return bool(snap.get("holding") and snap.get("ac_online")
                and percent >= target - slack)


def fmt_duration(seconds):
    if seconds is None:
        return "--"
    seconds = int(seconds)
    if seconds < 60:
        return "<1m"
    hours, mins = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def fmt_clock(seconds):
    """Duration as H:MM, for the compact panel readout."""
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    hours, mins = divmod(seconds // 60, 60)
    return f"{hours}:{mins:02d}"
