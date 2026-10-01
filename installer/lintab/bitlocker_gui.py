# SPDX-License-Identifier: MIT
"""“Unlock BitLocker Drive”: open an encrypted Windows drive from the LintabOS desktop.

A thin GTK front end over ``lintab-bitlocker`` (run through pkexec, so the recovery key goes to it
on stdin and never appears on a command line). Files open read-only by default.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import bitlocker  # noqa: E402

TOOL = "/usr/bin/lintab-bitlocker"


class UnlockWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Unlock BitLocker Drive", default_width=560, default_height=620)
        self.volumes: list[tuple[str, str]] = []
        self.mount_point: Optional[str] = None

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        self.set_content(view)
        view.set_content(Adw.Clamp(maximum_size=520, child=box))

        box.append(Gtk.Label(
            label="Open a Windows drive that is protected with BitLocker. You need its 48-digit recovery key "
                  "(account.microsoft.com/devices/recoverykey). Files open read-only.", wrap=True, xalign=0))

        self.drives = Gtk.StringList()
        self.drive_row = Adw.ComboRow(title="Drive", model=self.drives)
        self.key_row = Adw.PasswordEntryRow(title="Recovery key")
        group = Adw.PreferencesGroup()
        group.add(self.drive_row)
        group.add(self.key_row)
        box.append(group)

        self.button = Gtk.Button(label="Unlock", halign=Gtk.Align.CENTER)
        self.button.add_css_class("suggested-action")
        self.button.add_css_class("pill")
        self.button.connect("clicked", lambda _b: self._unlock())
        box.append(self.button)

        self.open_button = Gtk.Button(label="Open files", halign=Gtk.Align.CENTER, visible=False)
        self.open_button.add_css_class("pill")
        self.open_button.connect("clicked", lambda _b: self._open_files())
        box.append(self.open_button)

        self.status = Gtk.Label(label="", wrap=True, xalign=0.5)
        box.append(self.status)
        self._refresh()

    def _refresh(self) -> None:
        self.status.set_text("Looking for BitLocker drives…")

        def work() -> None:
            proc = subprocess.run(["pkexec", TOOL, "list"], capture_output=True, text=True)
            rows = [line.split("\t") for line in proc.stdout.splitlines() if line.startswith("/dev/")]
            GLib.idle_add(self._listed, rows)

        threading.Thread(target=work, daemon=True).start()

    def _listed(self, rows: list[list[str]]) -> bool:
        self.volumes = [(r[0], r[1] if len(r) > 1 else "") for r in rows]
        self.drives.splice(0, self.drives.get_n_items(), [f"{dev}  ({size})" for dev, size in self.volumes])
        self.status.set_text("" if self.volumes else "No BitLocker drives found.")
        self.button.set_sensitive(bool(self.volumes))
        return False

    def _unlock(self) -> None:
        if not self.volumes:
            return
        try:
            key = bitlocker.normalize_recovery_key(self.key_row.get_text())
        except bitlocker.BitLockerError as exc:
            self.status.set_text(str(exc))
            return
        device = self.volumes[self.drive_row.get_selected()][0]
        self.button.set_sensitive(False)
        self.status.set_text("Unlocking… this takes a few seconds.")

        def work() -> None:
            proc = subprocess.run(["pkexec", TOOL, "unlock", device, "--mount", "--key-stdin"],
                                  input=key + "\n", capture_output=True, text=True)
            GLib.idle_add(self._unlocked, proc.returncode, proc.stdout.strip(), proc.stderr.strip())

        threading.Thread(target=work, daemon=True).start()

    def _unlocked(self, rc: int, out: str, err: str) -> bool:
        self.button.set_sensitive(True)
        if rc != 0:
            self.status.set_text(err.replace("error: ", "") or "Could not unlock the drive.")
            return False
        self.mount_point = out.splitlines()[-1]
        self.status.set_text(f"Unlocked. Files are at {self.mount_point}")
        self.open_button.set_visible(True)
        self._open_files()
        return False

    def _open_files(self) -> None:
        if self.mount_point:
            Gio.AppInfo.launch_default_for_uri(f"file://{self.mount_point}", None)


class UnlockApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.UnlockBitLocker")
        self.connect("activate", lambda app: UnlockWindow(app).present())


def main() -> int:
    return UnlockApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
