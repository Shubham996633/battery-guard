#!/usr/bin/env bash
# Removes battery-guard. Restores the charge cap to 100% first, so you are
# not left with a capped battery and no tool to change it.
#
#   ./uninstall.sh              remove everything, keep your config
#   ./uninstall.sh --purge      also delete your config and health history
set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

ok() { printf '  \033[92m✓\033[0m %s\n' "$*"; }

printf '\n\033[1mStopping services\033[0m\n'
for unit in battery-tray.service battery-guard.service; do
    systemctl --user disable --now "$unit" 2>/dev/null || true
    ok "$unit stopped"
done

printf '\n\033[1mRemoving user files\033[0m\n'
rm -f "$HOME/.local/bin/"{battinfo,battery-guardd,battery-hud,battery-tray}
rm -rf "$HOME/.local/lib/battery-guard"
rm -f "$HOME/.config/systemd/user/"{battery-guard.service,battery-tray.service}
rm -f "$HOME/.local/share/applications/"{battery-hud.desktop,battery-tray.desktop}
rm -f "$HOME/.config/autostart/battery-tray.desktop"
rm -rf "$HOME/.local/share/gnome-shell/extensions/battery-time@local"
systemctl --user daemon-reload
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
ok "commands, library, services and menu entries removed"

if [ "$PURGE" = 1 ]; then
    rm -rf "$HOME/.config/battery-guard" "${XDG_STATE_HOME:-$HOME/.local/state}/battery-guard"
    ok "config and health history removed"
else
    ok "config and health history kept (use --purge to delete)"
fi

if [ -e /etc/systemd/system/battery-charge-limit.service ]; then
    printf '\n\033[1mRemoving the charge cap (needs sudo)\033[0m\n'
    sudo /usr/local/sbin/battery-charge-limit 100 2>/dev/null || true
    ok "charge limit restored to 100%"
    sudo systemctl disable --now battery-charge-limit.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/battery-charge-limit.service \
               /usr/lib/systemd/system-sleep/battery-charge-limit \
               /usr/local/sbin/battery-charge-limit \
               /etc/default/battery-charge-limit
    sudo systemctl daemon-reload
    ok "root components removed"
fi

printf '\nDone.\n'
