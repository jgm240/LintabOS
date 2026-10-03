# SPDX-License-Identifier: MIT
"""“Sleep & Power Button”: choose what the power button and closing the folio do, and see the sleep-mode status."""

from __future__ import annotations

import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import sleepmode  # noqa: E402

CHOICES = (
    ("suspend", "Suspend", "The usual choice: the screen turns off and the tablet uses very little power."),
    ("lock", "Lock the screen only", "The tablet stays fully on. Use this if suspend doesn't reliably wake back up "
     "on your tablet, even with deep sleep preferred below."),
    ("nothing", "Do nothing", "Nothing happens at all. The screen stays on and the battery drains — only for when "
     "the other two are both unusable."),
)


class SleepWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Sleep & Power Button", default_width=560, default_height=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=520, child=Gtk.ScrolledWindow(child=box, vexpand=True)))

        self.sleep_status = Gtk.Label(label="Checking the sleep mode…", wrap=True, xalign=0)
        box.append(self.sleep_status)

        actions = sleepmode.read_actions()
        self._groups: dict[str, dict[str, Gtk.CheckButton]] = {}
        for field, title in (("power", "Power button"), ("lid", "Closing the folio")):
            group = Adw.PreferencesGroup(title=title)
            buttons: dict[str, Gtk.CheckButton] = {}
            first = None
            for key, label, subtitle in CHOICES:
                check = Gtk.CheckButton()
                if first is None:
                    first = check
                else:
                    check.set_group(first)
                check.set_active(key == actions[field])
                check.connect("toggled", self._chosen, field, key, check)
                row = Adw.ActionRow(title=label, subtitle=subtitle, subtitle_lines=3, activatable_widget=check)
                row.add_prefix(check)
                group.add(row)
                buttons[key] = check
            self._groups[field] = buttons
            box.append(group)

        self.status = Gtk.Label(label="", wrap=True, xalign=0, css_classes=["dim-label"])
        box.append(self.status)
        box.append(Gtk.Label(
            label="If your tablet doesn't wake up after suspending, it's a known issue being worked on — see "
                  "“Hardware Report” to help find the cause. Choosing “Lock the screen only” avoids the freeze "
                  "entirely in the meantime.",
            wrap=True, xalign=0, css_classes=["dim-label"]))
        threading.Thread(target=self._check_sleep_mode, daemon=True).start()

    def _check_sleep_mode(self) -> None:
        status = sleepmode.read_mem_sleep()
        GLib.idle_add(self._sleep_mode_checked, status)

    def _sleep_mode_checked(self, status) -> bool:
        if status is None:
            self.sleep_status.set_text("Sleep mode: this platform offers no choice.")
        elif status.deep_available:
            self.sleep_status.set_text(f"Sleep mode: {status.current} (deep sleep preferred automatically at every start).")
        else:
            self.sleep_status.set_text(f"Sleep mode: {status.current} (this platform offers no more reliable alternative).")
        return False

    def _chosen(self, check: Gtk.CheckButton, field: str, key: str, _check2) -> None:
        if not check.get_active():
            return
        self.status.set_text("Saving…")
        power = key if field == "power" else next(k for k, b in self._groups["power"].items() if b.get_active())
        lid = key if field == "lid" else next(k for k, b in self._groups["lid"].items() if b.get_active())

        def work() -> None:
            rc = subprocess.run(["pkexec", sleepmode.SET_HELPER, power, lid], capture_output=True).returncode
            GLib.idle_add(self._saved, rc)

        threading.Thread(target=work, daemon=True).start()

    def _saved(self, rc: int) -> bool:
        self.status.set_text("Saved." if rc == 0 else "That didn't save (cancelled, or not allowed).")
        return False


class SleepApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.SleepMode")
        self.connect("activate", lambda app: SleepWindow(app).present())


def main() -> int:
    return SleepApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
