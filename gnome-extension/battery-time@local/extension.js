/* Battery Time & Cap — panel readout of remaining time + charge-cap control.
 *
 * Reads the same sysfs attributes as battinfo/battery-hud and the same
 * ~/.config/battery-guard/config.json, so all three agree on the target.
 */

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const SYSFS = '/sys/class/power_supply';
const LIMIT_TOOL = '/usr/local/sbin/battery-charge-limit';
const REFRESH_SECONDS = 10;

function readText(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        return new TextDecoder().decode(bytes).trim();
    } catch (_e) {
        return null;
    }
}

function readNum(path) {
    const raw = readText(path);
    if (raw === null)
        return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
}

function findBattery() {
    const dir = Gio.File.new_for_path(SYSFS);
    let iter;
    try {
        iter = dir.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
    } catch (_e) {
        return null;
    }
    let info;
    const names = [];
    while ((info = iter.next_file(null)) !== null) {
        if (info.get_name().startsWith('BAT'))
            names.push(info.get_name());
    }
    iter.close(null);
    names.sort();
    return names.length ? `${SYSFS}/${names[0]}` : null;
}

function loadTargets() {
    const path = `${GLib.get_home_dir()}/.config/battery-guard/config.json`;
    const raw = readText(path);
    let cfg = {};
    if (raw) {
        try {
            cfg = JSON.parse(raw);
        } catch (_e) {
            cfg = {};
        }
    }
    return {
        max: Number(cfg.target_max) || 85,
        min: Number(cfg.target_min) || 20,
    };
}

function fmtClock(seconds) {
    if (seconds === null || !Number.isFinite(seconds))
        return '--:--';
    const total = Math.floor(seconds / 60);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return `${h}:${m.toString().padStart(2, '0')}`;
}

function fmtLong(seconds) {
    if (seconds === null || !Number.isFinite(seconds))
        return '--';
    const total = Math.floor(seconds / 60);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return h ? `${h}h ${m.toString().padStart(2, '0')}m` : `${m}m`;
}

function snapshot() {
    const bat = findBattery();
    if (!bat)
        return null;

    const t = loadTargets();
    const percent = readNum(`${bat}/capacity`);
    const status = readText(`${bat}/status`) || 'Unknown';
    const nowUwh = readNum(`${bat}/energy_now`);
    const fullUwh = readNum(`${bat}/energy_full`);
    const designUwh = readNum(`${bat}/energy_full_design`);
    const powerUw = readNum(`${bat}/power_now`);
    const cap = readNum(`${bat}/charge_control_end_threshold`);

    const nowWh = nowUwh === null ? null : nowUwh / 1e6;
    const fullWh = fullUwh === null ? null : fullUwh / 1e6;
    const designWh = designUwh === null ? null : designUwh / 1e6;
    const watts = powerUw === null ? null : Math.abs(powerUw) / 1e6;

    const charging = status === 'Charging';
    const discharging = status === 'Discharging';

    let toTarget = null, toEmpty = null, toFloor = null;
    if (watts && watts > 0.05 && fullWh && nowWh !== null) {
        if (charging) {
            const targetWh = fullWh * t.max / 100;
            if (nowWh < targetWh)
                toTarget = (targetWh - nowWh) / watts * 3600;
        } else if (discharging) {
            toEmpty = nowWh / watts * 3600;
            const floorWh = fullWh * t.min / 100;
            if (nowWh > floorWh)
                toFloor = (nowWh - floorWh) / watts * 3600;
        }
    }

    return {
        percent, status, charging, discharging, watts, cap,
        health: fullWh && designWh ? fullWh / designWh * 100 : null,
        fullWh, designWh, toTarget, toEmpty, toFloor,
        target: t,
    };
}

const BatteryTimeIndicator = GObject.registerClass(
class BatteryTimeIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'Battery Time', false);

        this._label = new St.Label({
            text: '--',
            yAlign: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._label);

        this._headline = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._detail = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._health = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._headline);
        this.menu.addMenuItem(this._detail);
        this.menu.addMenuItem(this._health);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._capItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._capItem);

        const targets = loadTargets();
        const setCap = this._addAction(`Limit charging to ${targets.max}%`, () =>
            this._runLimit(String(targets.max)));
        const setFull = this._addAction('Allow full charge (travel)', () =>
            this._runLimit('100'));
        void setCap; void setFull;

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addAction('Open Battery app', () => this._spawn(
            [`${GLib.get_home_dir()}/.local/bin/battery-hud`]));

        this._refresh();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, REFRESH_SECONDS, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _addAction(label, fn) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', fn);
        this.menu.addMenuItem(item);
        return item;
    }

    _spawn(argv) {
        try {
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        } catch (e) {
            Main.notify('Battery Time & Cap', `Could not run ${argv[0]}: ${e.message}`);
        }
    }

    // pkexec pops the normal GNOME password dialog; no sudoers changes needed.
    _runLimit(value) {
        this._spawn(['pkexec', LIMIT_TOOL, value]);
    }

    _refresh() {
        const s = snapshot();
        if (!s) {
            this._label.set_text('no battery');
            return;
        }

        const p = s.percent ?? 0;
        if (s.charging) {
            this._label.set_text(`${p}% ↑ ${fmtClock(s.toTarget)}`);
            this._headline.label.set_text(`Charging — ${fmtLong(s.toTarget)} to the ${s.target.max}% cap`);
        } else if (s.discharging) {
            this._label.set_text(`${p}% ↓ ${fmtClock(s.toEmpty)}`);
            this._headline.label.set_text(`On battery — ${fmtLong(s.toEmpty)} to empty`);
        } else {
            this._label.set_text(`${p}% · cap`);
            this._headline.label.set_text(`Holding at ${p}% — charging stopped by the EC limit`);
        }

        const watts = s.watts ? `${s.watts.toFixed(1)} W` : 'idle';
        if (s.discharging)
            this._detail.label.set_text(`${watts} · ${fmtLong(s.toFloor)} to the ${s.target.min}% floor`);
        else
            this._detail.label.set_text(`${watts} · ${s.status}`);

        const health = s.health === null ? '--' : `${s.health.toFixed(1)}%`;
        const wh = s.fullWh && s.designWh
            ? ` (${s.fullWh.toFixed(2)} / ${s.designWh.toFixed(2)} Wh)` : '';
        this._health.label.set_text(`Health ${health}${wh}`);

        const drifted = s.cap !== null && s.cap !== s.target.max;
        this._capItem.label.set_text(drifted
            ? `Charge limit ${s.cap}% — target is ${s.target.max}%`
            : `Charge limit ${s.cap ?? '--'}% (enforced in firmware)`);
    }

    destroy() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        super.destroy();
    }
});

export default class BatteryTimeExtension extends Extension {
    enable() {
        this._indicator = new BatteryTimeIndicator();
        Main.panel.addToStatusArea('battery-time@local', this._indicator, 0, 'right');
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
