# battery-guard

Stop your laptop battery from sitting at 100%, and actually get told when it drops too low.

Most Linux desktops show you a battery percentage and nothing else. No charge limit, no alert
before the pack hits a damaging state of charge, and no estimate of how long you have left
unless you go hunting. `battery-guard` fixes all three, using the battery controller your
laptop already has.

```
  88%  ████████████████████████|░░░  (| = 85% cap)

  State        Discharging  (on battery)   4.7 W
  To empty     8h 33m
  To 20% floor 6h 36m   (plug in around then)
  Charge cap   85% enforced
  Health       100.0%   45.51 / 45.51 Wh · now 40.00 Wh
```

## Why a cap instead of a notification

Lithium-ion cells age fastest when held at a high state of charge. The usual advice is to keep
them between roughly 20% and 85%, but a notification saying "unplug now" only helps if you are
sitting at the machine.

So `battery-guard` does not notify you about the upper limit — it writes the limit to your
laptop's **embedded controller** through `charge_control_end_threshold`. The firmware then
refuses to charge past it. That holds at the login screen, with the session locked, while
suspended, and after a reboot. Notifications are used only for the lower limit, where you
genuinely have to act.

## What you get

| | |
|---|---|
| **Charge cap** | Enforced in firmware, re-applied on boot and resume. Default 85%. |
| **Tray indicator** | `88% ↓ 8:33` in the panel — percentage *and* remaining time. Menu shows wattage, time to the low-battery floor, pack health, and switches the cap between your target and 100% for travel days. |
| **Low-battery alerts** | Default 20 / 15 / 10 / 5%, each with time remaining and a sound. `≤10%` is critical urgency so it will not auto-dismiss. Hysteresis prevents repeat spam at a boundary. |
| **Desktop app** | A ring gauge with every estimate, live at 2 s. |
| **`battinfo`** | The terminal readout above. `--json` for scripting, `--panel` for status bars. |
| **Drift detection** | If firmware resets the cap, you get told, instead of silently charging to 100% again. |

## Requirements

- Linux with systemd, and a desktop that shows notifications
- Python 3 with PyGObject
- **For the charge cap:** a kernel driver exposing `charge_control_end_threshold`. Check with:

  ```sh
  ls /sys/class/power_supply/BAT*/charge_control_end_threshold
  ```

  Common on ASUS (`asus_wmi`), ThinkPad (`thinkpad_acpi`), Huawei (`huawei_wmi`), and some
  others. If the file is absent, everything except the cap still works — the installer detects
  this and skips that part.

  Note that some firmware only accepts fixed steps (often 60/80/100) and silently clamps other
  values. `battery-charge-limit` reads the value back and warns you if that happened.

- **For the tray icon:** an AppIndicator/StatusNotifierItem host. GNOME needs an extension for
  this (`gnome-shell-extension-appindicator`); KDE, Cinnamon, XFCE and Zorin have it built in.

Debian/Ubuntu dependencies:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-ayatanaappindicator3-0.1 libnotify-bin
```

## Install

```sh
git clone https://github.com/Shubham996633/battery-guard.git
cd battery-guard
./install.sh                 # 85% cap
./install.sh --limit 80      # pick your own
./install.sh --no-root       # skip the cap, no sudo needed
```

The installer checks your hardware, reports missing dependencies, never overwrites an existing
config, and prints the readout when it finishes. Everything user-level goes to `~/.local` and
`~/.config`; only the cap touches the system, because it has to.

## Usage

```sh
battinfo                 # terminal readout
battinfo --json          # machine readable
battinfo --panel         # one compact line for a status bar
```

- **Window:** app menu → *Battery*
- **Tray gone after you quit it?** app menu → *Battery Tray*, or
  `systemctl --user restart battery-tray.service`

Change the cap:

```sh
sudo battery-charge-limit 80        # tells the firmware
sudo battery-charge-limit --show    # what the firmware currently thinks
```

Then set `target_max` to the same value in `~/.config/battery-guard/config.json`, which drives
the notifications and estimates. If the two disagree, the tray and a notification will say so
rather than letting you believe a limit is active when it is not.

## Configuration

`~/.config/battery-guard/config.json`, picked up on the next poll — no restart needed:

```json
{
  "target_max": 85,
  "target_min": 20,
  "low_steps": [20, 15, 10, 5],
  "notify_full": true,
  "notify_cap_drift": true,
  "poll_seconds": 20,
  "sound": true
}
```

| Key | Meaning |
|---|---|
| `target_max` | Charge cap. Also where the "charging stopped" notification fires. |
| `target_min` | The floor used for the "time until you should plug in" estimate. |
| `low_steps` | Percentages that raise a notification while discharging. |
| `notify_full` | Notify once when charging reaches `target_max`. |
| `notify_cap_drift` | Warn if the firmware limit no longer matches `target_max`. |
| `poll_seconds` | How often the daemon checks. |
| `sound` | Play a sound with alerts, if a player is available. |

## How it works

```
sysfs /sys/class/power_supply/BAT0/    ─┐
  capacity, status, energy_now,         │
  energy_full, power_now,               ├─→ lib/battlib.py  (single source of truth)
  charge_control_end_threshold          │        │
config.json (your targets)             ─┘        │
                                                 ├─→ battinfo        terminal
                                                 ├─→ battery-tray    panel indicator
                                                 ├─→ battery-hud     window
                                                 └─→ battery-guardd  notifications
```

Estimates come from energy and power readings rather than the percentage, so they reflect what
the pack is actually doing: time to empty is `energy_now / power_now`, and time to the cap is
`(energy_full × target_max/100 − energy_now) / power_now`.

Three pieces keep the cap alive:

- `battery-charge-limit.service` applies it at boot
- `/usr/lib/systemd/system-sleep/battery-charge-limit` re-applies it after suspend, because
  some firmware drops the threshold on resume
- `notify_cap_drift` tells you if it ever slips anyway

Privileged writes go through `pkexec`, so the GUI raises a normal password prompt. The
installer deliberately does **not** add a `sudoers` rule.

## Cost

Measured on an ASUS VivoBook (Zorin OS 18, GNOME 46), steady state over a 30-second sample:

| Component | Memory | CPU |
|---|---|---|
| `battery-guardd` | 13.3 MB | below the kernel's 10 ms measurement resolution |
| `battery-tray` | 40.7 MB | same |

The window uses ~148 MB while open and nothing when closed.

## Uninstall

```sh
./uninstall.sh            # keeps your config
./uninstall.sh --purge    # deletes it too
```

This restores the charge limit to 100% **before** removing the tool, so you are never left with
a capped battery and no way to change it.

## Optional GNOME Shell extension

`gnome-extension/battery-time@local/` is an alternative to the tray icon: a native panel
indicator, lighter because it runs inside the shell, but it needs a logout to load and can
break on GNOME upgrades. The tray applet is the default for that reason. To use it instead:

```sh
cp -r gnome-extension/battery-time@local ~/.local/share/gnome-shell/extensions/
systemctl --user disable --now battery-tray.service    # avoid two indicators
# log out and back in, then:
gnome-extensions enable battery-time@local
```

## Verified on

Zorin OS 18.1 (Ubuntu 24.04 base), GNOME 46, Wayland, kernel 7.0, ASUS VivoBook K3502ZA.
The sysfs paths and systemd layout are standard, so other distributions should work; reports
welcome.

## License

MIT — see [LICENSE](LICENSE).
