# SPDX-License-Identifier: MIT
"""Experimental: write Windows' real onetimeadvancedoptions BCD flag (what Shift+Restart actually sets), with an
automatic backup-and-verify safety net. See lintab.bcdbackup for exactly what that safety net does and why this
is labeled experimental rather than folded into the confirmed-safe "Restart into Windows Recovery" tool.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import bcdbackup  # noqa: E402

WARNING = ("Experimental. This has been verified to produce a structurally valid BCD file against real Windows "
          "setup-media samples, but has not yet been confirmed on a real, already-installed Windows system. A "
          "backup is always taken first and automatically restored if anything fails to verify - see Support "
          "for details - but this is still new. Only use it if you understand that.")


class BcdWriteWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Windows Boot Configuration (Experimental)",
                         default_width=560, default_height=560)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=520, child=box))

        banner = Adw.Banner(title=WARNING, revealed=True)
        box.append(banner)

        group = Adw.PreferencesGroup()
        write_row = Adw.ActionRow(
            title="Write onetimeadvancedoptions",
            subtitle="Backs up the live BCD, writes the flag, verifies the result, and automatically restores "
                     "the backup if verification fails. Requires at least 40% battery.",
            activatable=True)
        write_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        write_row.connect("activated", lambda _r: self._run("write"))
        group.add(write_row)

        restore_row = Adw.ActionRow(
            title="Restore backed-up BCD",
            subtitle="Puts the BCD back exactly as it was before the last write, independent of whether that "
                     "write looked like it worked.",
            activatable=True)
        restore_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        restore_row.connect("activated", lambda _r: self._run("restore"))
        group.add(restore_row)

        describe_row = Adw.ActionRow(
            title="Describe current boot entry",
            subtitle="Read-only - lists every element on the default boot entry (e.g. whether "
                     "onetimeadvancedoptions, recoverysequence, or anything recovery-related is actually there), "
                     "without touching Windows or writing anything.",
            activatable=True)
        describe_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        describe_row.connect("activated", lambda _r: self._run("describe"))
        group.add(describe_row)
        box.append(group)

        self.status = Gtk.Label(label="", wrap=True, xalign=0, css_classes=["dim-label"])
        box.append(self.status)
        self.spinner = Gtk.Spinner()
        box.append(self.spinner)

        scroller = Gtk.ScrolledWindow(vexpand=True, has_frame=True)
        self.output = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                   top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        scroller.set_child(self.output)
        box.append(scroller)

        self._buttons = (write_row, restore_row, describe_row)

    def _run(self, mode: str) -> None:
        for row in self._buttons:
            row.set_sensitive(False)
        self.status.set_text("Working…")
        self.spinner.start()

        def work() -> None:
            try:
                proc = subprocess.run(["pkexec", bcdbackup.HELPER, mode], capture_output=True, text=True)
            except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the UI is stuck "Working…" forever
                GLib.idle_add(self._done, 1, str(exc))
                return
            GLib.idle_add(self._done, proc.returncode, (proc.stdout or proc.stderr or "").strip())

        threading.Thread(target=work, daemon=True).start()

    def _done(self, returncode: int, message: str) -> bool:
        self.spinner.stop()
        for row in self._buttons:
            row.set_sensitive(True)
        self.status.set_text("Done." if returncode == 0 else f"Failed (exit {returncode}).")
        self.output.get_buffer().set_text(message)
        return False


class BcdWriteApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.BcdWrite")
        self.connect("activate", lambda app: BcdWriteWindow(app).present())


def main() -> int:
    return BcdWriteApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
