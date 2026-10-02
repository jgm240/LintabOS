# SPDX-License-Identifier: MIT
"""“LintabOS Updates”: check GitHub for a newer LintabOS release and install it.

Checking is unprivileged. Installing runs ``pkexec lintab-update apply`` (password prompt), which downloads the
release, verifies its signature and only then installs it. Debian's own packages (kernel, GNOME, security fixes)
are updated through GNOME Software.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

TOOL = "/usr/bin/lintab-update"


class UpdateWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="LintabOS Updates", default_width=560, default_height=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=520, child=Gtk.ScrolledWindow(child=box, vexpand=True)))

        self.group = Adw.PreferencesGroup(title="LintabOS")
        self.row_installed = Adw.ActionRow(title="Installed", subtitle="…")
        self.row_latest = Adw.ActionRow(title="Newest release", subtitle="…")
        self.group.add(self.row_installed)
        self.group.add(self.row_latest)
        box.append(self.group)

        self.status = Gtk.Label(label="", wrap=True, xalign=0)
        box.append(self.status)
        self.notes = Gtk.Label(label="", wrap=True, xalign=0, selectable=True)
        box.append(self.notes)

        self.update_button = Gtk.Button(label="Update now", halign=Gtk.Align.CENTER, visible=False)
        self.update_button.add_css_class("suggested-action")
        self.update_button.add_css_class("pill")
        self.update_button.connect("clicked", lambda _b: self._apply())
        box.append(self.update_button)

        self.rollback_button = Gtk.Button(label="", halign=Gtk.Align.CENTER, visible=False)
        self.rollback_button.add_css_class("pill")
        self.rollback_button.connect("clicked", lambda _b: self._rollback())
        box.append(self.rollback_button)

        self.check_button = Gtk.Button(label="Check again", halign=Gtk.Align.CENTER)
        self.check_button.add_css_class("pill")
        self.check_button.connect("clicked", lambda _b: self._check())
        box.append(self.check_button)

        box.append(Gtk.Label(
            label="This updates LintabOS's own parts. Debian's system packages (kernel, GNOME, security fixes) "
                  "are updated in Software. Checking contacts github.com; turn the automatic daily check off in "
                  "/etc/lintabos/update.conf.", wrap=True, xalign=0, css_classes=["dim-label"]))
        self._check()

    def _check(self) -> None:
        self.check_button.set_sensitive(False)
        self.update_button.set_visible(False)
        self.status.set_text("Checking GitHub…")

        def work() -> None:
            proc = subprocess.run([TOOL, "check", "--json"], capture_output=True, text=True)
            try:
                info = json.loads(proc.stdout)
            except ValueError:
                info = {"error": proc.stderr.strip() or "The updater could not run."}
            GLib.idle_add(self._checked, info)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, info: dict) -> bool:
        self.check_button.set_sensitive(True)
        self.row_installed.set_subtitle(info.get("installed") or "unknown")
        self.rollback_button.set_visible(bool(info.get("rollback")))
        self.rollback_button.set_label(f"Go back to {info.get('rollback')}")
        if info.get("error"):
            self.row_latest.set_subtitle("unknown")
            self.status.set_text(info["error"])
            self.notes.set_text("")
            return False
        latest = info.get("latest")
        self.row_latest.set_subtitle((latest or "none yet") + (" (pre-release)" if info.get("prerelease") else ""))
        if info.get("available"):
            self.status.set_text(f"LintabOS {latest} is available.")
            self.notes.set_text(info.get("notes", "")[:2000])
            self.update_button.set_visible(True)
        else:
            self.status.set_text("LintabOS is up to date.")
            self.notes.set_text("")
        return False

    def _apply(self) -> None:
        self.update_button.set_sensitive(False)
        self.check_button.set_sensitive(False)
        self.status.set_text("Installing the update… (enter your password when asked)")

        def work() -> None:
            proc = subprocess.run(["pkexec", TOOL, "apply", "--yes"], capture_output=True, text=True)
            GLib.idle_add(self._applied, proc.returncode, (proc.stdout + proc.stderr).strip())

        threading.Thread(target=work, daemon=True).start()

    def _rollback(self) -> None:
        self.rollback_button.set_sensitive(False)
        self.status.set_text("Going back to the earlier version… (enter your password when asked)")

        def work() -> None:
            proc = subprocess.run(["pkexec", TOOL, "rollback", "--yes"], capture_output=True, text=True)
            GLib.idle_add(self._rolled_back, proc.returncode, (proc.stdout + proc.stderr).strip())

        threading.Thread(target=work, daemon=True).start()

    def _rolled_back(self, rc: int, output: str) -> bool:
        self.rollback_button.set_sensitive(True)
        self.status.set_text("Rolled back. Restart the tablet to be sure every part is using it." if rc == 0
                             else (output.replace("error: ", "") or "Rolling back did not work."))
        if rc == 0:
            self._check()
        return False

    def _applied(self, rc: int, output: str) -> bool:
        self.update_button.set_sensitive(True)
        self.check_button.set_sensitive(True)
        if rc == 0:
            self.status.set_text("Updated. Restart the tablet to be sure every part is using the new version.")
            self.update_button.set_visible(False)
            self._check_quietly()
        else:
            self.status.set_text(output.replace("error: ", "") or "The update did not install.")
        return False

    def _check_quietly(self) -> None:
        proc = subprocess.run([TOOL, "check", "--json"], capture_output=True, text=True)
        try:
            self.row_installed.set_subtitle(json.loads(proc.stdout).get("installed") or "unknown")
        except ValueError:
            pass


class UpdateApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.Updates")
        self.connect("activate", lambda app: UpdateWindow(app).present())


def main() -> int:
    return UpdateApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
