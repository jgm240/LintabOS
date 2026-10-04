# SPDX-License-Identifier: MIT
"""“Restart into Windows”: a normal restart, an attempt at Windows Recovery, or the way to Safe Mode.

See lintab.winrecovery for exactly what "Recovery" does — tested on a real Duet 3, with real, honest results.
"""

from __future__ import annotations

import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import winrecovery  # noqa: E402

CHOICES = (
    ("normal", "Restart into Windows", "A normal restart, straight into Windows."),
    ("recovery", "Restart into Windows Recovery",
     winrecovery.RECOVERY_EFFECT + " " + winrecovery.RECOVERY_STEPS),
    ("safe", "Restart into Windows Safe Mode",
     "Same attempt as Recovery, since Safe Mode lives inside Windows' own recovery menu, not as a separate boot "
     "choice. " + winrecovery.SAFE_MODE_STEPS),
)


class RestartWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Restart into Windows", default_width=560, default_height=460)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=520, child=box))

        group = Adw.PreferencesGroup()
        for key, label, subtitle in CHOICES:
            row = Adw.ActionRow(title=label, subtitle=subtitle, subtitle_lines=5, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, k=key: self._go(k))
            group.add(row)
        box.append(group)
        self.status = Gtk.Label(label="", wrap=True, xalign=0, css_classes=["dim-label"])
        box.append(self.status)

    def _go(self, key: str) -> None:
        mode = "normal" if key == "normal" else "recovery"
        self.status.set_text("Restarting…")
        subprocess.Popen(["pkexec", winrecovery.HELPER, mode])


class RestartApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.RestartWindows")
        self.connect("activate", lambda app: RestartWindow(app).present())


def main() -> int:
    return RestartApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
