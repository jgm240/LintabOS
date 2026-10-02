# SPDX-License-Identifier: MIT
"""“School Mode”: one big switch that turns every camera off. Turning it off asks for an administrator's password."""

from __future__ import annotations

import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import school  # noqa: E402


class SchoolWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="School Mode", default_width=520, default_height=420)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=480, child=box))

        group = Adw.PreferencesGroup()
        self.switch = Adw.SwitchRow(title="School mode", subtitle="All cameras off")
        self._busy = False
        self._handler = self.switch.connect("notify::active", self._toggled)
        group.add(self.switch)
        box.append(group)
        self.status = Gtk.Label(label="", wrap=True, xalign=0)
        box.append(self.status)
        box.append(Gtk.Label(
            label="Turning school mode on is instant. Turning it off needs an administrator's password, so a student "
                  "account can't do it. It blocks the built-in cameras and USB webcams until it is turned off; "
                  "restart the tablet if the status says a driver is still loaded.",
            wrap=True, xalign=0, css_classes=["dim-label"]))
        self._refresh()

    def _refresh(self) -> None:
        status = school.inspect()
        self._busy = True
        self.switch.handler_block(self._handler)
        self.switch.set_active(status.on)
        self.switch.handler_unblock(self._handler)
        self._busy = False
        if not status.on:
            self.status.set_text("Cameras are available.")
        elif status.effective:
            self.status.set_text("School mode is on. No camera can be used.")
        else:
            self.status.set_text("School mode is on, but a camera driver is still loaded. It unloads on the next restart.")

    def _toggled(self, *_args) -> None:
        if self._busy:
            return
        want_on = self.switch.get_active()
        self.status.set_text("Working…" if want_on else "Enter an administrator's password…")

        def work() -> None:
            helper = school.HELPER_ON if want_on else school.HELPER_OFF
            rc = subprocess.run(["pkexec", helper], capture_output=True).returncode
            GLib.idle_add(self._done, rc)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, rc: int) -> bool:
        self._refresh()   # shows the real state, so a cancelled password prompt leaves the switch where it truly is
        if rc != 0:
            self.status.set_text("That didn't change anything (cancelled or not allowed).")
        return False


class SchoolApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.SchoolMode")
        self.connect("activate", lambda app: SchoolWindow(app).present())


def main() -> int:
    return SchoolApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
