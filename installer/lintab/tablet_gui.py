# SPDX-License-Identifier: MIT
"""“Tablet & Folio”: choose how the on-screen keyboard behaves, including a mode for folios with faulty keys."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import tablet  # noqa: E402

CHOICES = (
    ("auto", "Automatic", "On-screen keyboard appears when the folio is detached and hides when it is attached."),
    ("assist", "Folio not working",
     "On-screen keyboard always available, even with the folio attached. The folio keeps working: type with the keys "
     "that work, tap the ones that don't."),
    ("tablet", "Always tablet", "On-screen keyboard always on."),
    ("laptop", "Always laptop", "On-screen keyboard never opens by itself."),
)


class TabletWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Tablet & Folio", default_width=520, default_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=480, child=box))

        self.status = Gtk.Label(label="", wrap=True, xalign=0)
        box.append(self.status)
        group = Adw.PreferencesGroup(title="On-screen keyboard")
        first = None
        self._rows: dict[str, Gtk.CheckButton] = {}
        current = tablet.read_mode()
        for mode, title, subtitle in CHOICES:
            check = Gtk.CheckButton()
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.set_active(mode == current)
            check.connect("toggled", self._chosen, mode)
            row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_lines=3, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
            self._rows[mode] = check
        box.append(group)
        box.append(Gtk.Label(
            label="None of these modes ever turns the folio off. Many folios ship with a few unreliable keys; “Folio not "
                  "working” is for those.",
            wrap=True, xalign=0, css_classes=["dim-label"]))
        self._refresh_status()

    def _refresh_status(self) -> None:
        attached = tablet.keyboard_attached(tablet.current_devices())
        self.status.set_text("The folio keyboard is attached." if attached else "No keyboard is attached.")

    def _chosen(self, check: Gtk.CheckButton, mode: str) -> None:
        if not check.get_active():
            return
        tablet.write_mode(mode)
        tablet.apply_state(tablet.decide(mode, tablet.current_devices()))
        self._refresh_status()


class TabletApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.TabletSettings")
        self.connect("activate", lambda app: TabletWindow(app).present())


def main() -> int:
    return TabletApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
