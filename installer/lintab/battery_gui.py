# SPDX-License-Identifier: MIT
"""“Battery”: health, cycles and (where the firmware allows) a charge limit."""

from __future__ import annotations

import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import battery  # noqa: E402


class BatteryWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Battery", default_width=520, default_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=480, child=box))
        batteries = battery.read_batteries()
        if not batteries:
            box.append(Gtk.Label(label="No battery found.", xalign=0))
            return
        info = batteries[0]
        group = Adw.PreferencesGroup(title="Battery")
        group.add(Adw.ActionRow(title="Charge", subtitle=f"{info.capacity if info.capacity is not None else '?'} % ({info.status or 'unknown'})"))
        group.add(Adw.ActionRow(title="Health", subtitle=battery.describe_health(info.health), subtitle_lines=3))
        if info.cycles is not None:
            group.add(Adw.ActionRow(title="Charge cycles", subtitle=str(info.cycles)))
        box.append(group)

        self.limit = Adw.SwitchRow(title="Stop charging at 80 %", subtitle="Wears the battery less if the tablet is often plugged in.")
        self.status = Gtk.Label(label="", wrap=True, xalign=0, css_classes=["dim-label"])
        if info.limit_supported:
            self.limit.set_active((info.threshold or 100) <= 80)
            self._handler = self.limit.connect("notify::active", self._toggled)
        else:
            self.limit.set_sensitive(False)
            self.limit.set_subtitle("Not offered by this tablet's firmware.")
        lim_group = Adw.PreferencesGroup(title="Charge limit")
        lim_group.add(self.limit)
        box.append(lim_group)
        box.append(self.status)

    def _toggled(self, *_args) -> None:
        value = "80" if self.limit.get_active() else "off"
        self.status.set_text("Enter an administrator's password…")

        def work() -> None:
            rc = subprocess.run(["pkexec", battery.HELPER, "set", value], capture_output=True).returncode
            GLib.idle_add(self._done, rc)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, rc: int) -> bool:
        info = battery.read_batteries()[0]
        self.limit.handler_block(self._handler)
        self.limit.set_active((info.threshold or 100) <= 80)
        self.limit.handler_unblock(self._handler)
        self.status.set_text("Saved." if rc == 0 else "That didn't change anything (cancelled or not allowed).")
        return False


class BatteryApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.Battery")
        self.connect("activate", lambda app: BatteryWindow(app).present())


def main() -> int:
    return BatteryApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
