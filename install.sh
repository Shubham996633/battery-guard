#!/usr/bin/env bash
# battery-guard installer.
#
#   ./install.sh                 install with an 85% charge cap
#   ./install.sh --limit 80      install with a different cap
#   ./install.sh --no-root       user parts only, skip the cap (no sudo)
#
# User parts land in ~/.local and ~/.config. The charge cap needs root
# because it writes to sysfs and installs the units that re-apply it on
# boot and resume.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMIT=85
DO_ROOT=1

while [ $# -gt 0 ]; do
    case "$1" in
        --limit) LIMIT="${2:?--limit needs a value}"; shift 2 ;;
        --no-root) DO_ROOT=0; shift ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ "$LIMIT" =~ ^([0-9]|[1-9][0-9]|100)$ ]] || { echo "--limit must be 0-100" >&2; exit 2; }

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[92m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[93m!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- checks
say "Checking this machine"

BAT="$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1 || true)"
if [ -z "$BAT" ]; then
    echo "No battery found under /sys/class/power_supply - nothing to do." >&2
    exit 1
fi
ok "battery: $(basename "$BAT")"

CAP_FILE="$BAT/charge_control_end_threshold"
HAS_CAP=0
if [ -e "$CAP_FILE" ]; then
    HAS_CAP=1
    ok "charge cap supported (current: $(cat "$CAP_FILE")%)"
else
    warn "no charge_control_end_threshold - your kernel/laptop cannot cap charging."
    warn "Notifications, tray and estimates still work; the cap will be skipped."
    DO_ROOT=0
fi

MISSING=()
command -v notify-send >/dev/null || MISSING+=("libnotify-bin")
python3 -c "import gi" 2>/dev/null || MISSING+=("python3-gi")
python3 -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null || MISSING+=("libgtk-4-1 gir1.2-gtk-4.0")
python3 -c "import gi; gi.require_version('Adw','1')" 2>/dev/null || MISSING+=("gir1.2-adw-1")
python3 -c "import gi; gi.require_version('AyatanaAppIndicator3','0.1')" 2>/dev/null \
    || python3 -c "import gi; gi.require_version('AppIndicator3','0.1')" 2>/dev/null \
    || MISSING+=("gir1.2-ayatanaappindicator3-0.1")

if [ ${#MISSING[@]} -gt 0 ]; then
    warn "missing packages: ${MISSING[*]}"
    warn "install them with:  sudo apt install ${MISSING[*]}"
else
    ok "all runtime dependencies present"
fi

# ---------------------------------------------------------------- user
say "Installing user components"

install -d "$HOME/.local/lib/battery-guard" "$HOME/.local/bin" \
           "$HOME/.config/battery-guard" "$HOME/.config/systemd/user" \
           "$HOME/.local/share/applications"

install -m 0644 "$SRC/lib/battlib.py" "$HOME/.local/lib/battery-guard/battlib.py"
ok "library -> ~/.local/lib/battery-guard"

install -m 0755 "$SRC"/bin/* "$HOME/.local/bin/"
ok "commands -> ~/.local/bin (battinfo, battery-hud, battery-tray, battery-guardd)"

if [ -f "$HOME/.config/battery-guard/config.json" ]; then
    ok "config already exists, left untouched"
else
    sed "s/\"target_max\": [0-9]*/\"target_max\": $LIMIT/" "$SRC/config/config.json" \
        > "$HOME/.config/battery-guard/config.json"
    ok "config -> ~/.config/battery-guard/config.json (cap $LIMIT%)"
fi

for f in "$SRC"/desktop/*.desktop; do
    sed "s|@HOME@|$HOME|g" "$f" > "$HOME/.local/share/applications/$(basename "$f")"
done
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
ok "menu entries -> Battery, Battery Tray"

install -m 0644 "$SRC"/systemd/user/*.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
ok "user services installed"

# ---------------------------------------------------------------- root
if [ "$DO_ROOT" = 1 ]; then
    say "Installing the charge cap (needs sudo)"
    sudo install -m 0755 "$SRC/sbin/battery-charge-limit" /usr/local/sbin/battery-charge-limit
    sudo tee /etc/default/battery-charge-limit >/dev/null <<< "BATTERY_CHARGE_LIMIT=$LIMIT"
    sudo install -m 0644 "$SRC/systemd/system/battery-charge-limit.service" \
        /etc/systemd/system/battery-charge-limit.service
    sudo install -m 0755 "$SRC/systemd/system-sleep/battery-charge-limit" \
        /usr/lib/systemd/system-sleep/battery-charge-limit
    sudo systemctl daemon-reload
    sudo systemctl enable --now battery-charge-limit.service
    ok "cap set to $LIMIT% and re-applied on every boot and resume"
    [ "$HAS_CAP" = 1 ] && ok "EC reports: $(cat "$CAP_FILE")%"
else
    say "Skipping the charge cap"
    warn "run with sudo access to enable it, or apply manually:"
    warn "  sudo install -m755 $SRC/sbin/battery-charge-limit /usr/local/sbin/"
fi

# ---------------------------------------------------------------- start
say "Starting services"
systemctl --user enable --now battery-guard.service
ok "notifications running (battery-guard.service)"
systemctl --user enable --now battery-tray.service
ok "tray indicator running (battery-tray.service)"

say "Done"
printf '  Terminal readout:   battinfo\n'
printf '  Window:             app menu -> "Battery"\n'
printf '  Tray back after quitting: app menu -> "Battery Tray"\n'
printf '  Change the cap:     sudo battery-charge-limit <0-100>\n'
printf '                      then edit target_max in ~/.config/battery-guard/config.json\n\n'

command -v battinfo >/dev/null && battinfo || "$HOME/.local/bin/battinfo"
