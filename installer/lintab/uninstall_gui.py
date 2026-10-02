# SPDX-License-Identifier: MIT
"""“Remove LintabOS”: give the space back to Windows. Runs from the live USB, as root (see lintab.uninstall)."""

from __future__ import annotations

import sys
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import disks, uninstall  # noqa: E402
from .plan import PlanError  # noqa: E402


class RemoveWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Remove LintabOS", default_width=640, default_height=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                           margin_start=18, margin_end=18)
        view.set_content(Adw.Clamp(maximum_size=560, child=Gtk.ScrolledWindow(child=self.box, vexpand=True)))
        self.plan: Optional[uninstall.UninstallPlan] = None
        self.status = Gtk.Label(label="Looking for LintabOS…", wrap=True, xalign=0)
        self.box.append(self.status)
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self) -> None:
        try:
            found = [d for d in disks.list_disks() if d.label == "gpt" and uninstall.find_lintabos(d)]
            if not found:
                raise PlanError("No LintabOS installation was found on this computer.")
            if len(found) > 1:
                raise PlanError("LintabOS was found on more than one disk. Remove it with `lintab-uninstall apply DISK`.")
            plan = uninstall.plan_uninstall(found[0])
            GLib.idle_add(self._show_plan, plan, None)
        except PlanError as exc:
            GLib.idle_add(self._show_plan, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            GLib.idle_add(self._show_plan, None, f"Could not look at the disks: {exc}")

    def _show_plan(self, plan: Optional[uninstall.UninstallPlan], error: Optional[str]) -> bool:
        if error or plan is None:
            self.status.set_text(error or "Nothing to do.")
            return False
        self.plan = plan
        self.status.set_text("This is what will happen. Nothing has been changed yet.")
        group = Adw.PreferencesGroup()
        for line in plan.summary:
            group.add(Adw.ActionRow(title=line, title_lines=4))
        self.box.append(group)
        self.confirm = Adw.SwitchRow(title="I understand LintabOS and everything saved in it will be deleted")
        confirm_group = Adw.PreferencesGroup()
        confirm_group.add(self.confirm)
        self.box.append(confirm_group)
        self.button = Gtk.Button(label="Remove LintabOS", halign=Gtk.Align.CENTER)
        self.button.add_css_class("destructive-action")
        self.button.add_css_class("pill")
        self.button.connect("clicked", lambda _b: self._go())
        self.box.append(self.button)
        self.bar = Gtk.ProgressBar(visible=False)
        self.box.append(self.bar)
        return False

    def _go(self) -> None:
        if not self.confirm.get_active():
            self.status.set_text("Turn on the switch to confirm.")
            return
        self.button.set_sensitive(False)
        self.confirm.set_sensitive(False)
        self.bar.set_visible(True)
        self.status.set_text("Removing LintabOS… keep the tablet plugged in.")

        def progress(i: int, n: int, description: str) -> None:
            GLib.idle_add(self._progress, i / n, description)

        def work() -> None:
            try:
                warnings = uninstall.apply_uninstall(self.plan, progress=progress)
                GLib.idle_add(self._finished, None, warnings)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                GLib.idle_add(self._finished, str(exc), [])

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, fraction: float, description: str) -> bool:
        self.bar.set_fraction(fraction)
        self.status.set_text(description)
        return False

    def _finished(self, error: Optional[str], warnings: list[str]) -> bool:
        if error:
            self.status.set_text(f"It didn't finish:\n{error}")
        else:
            self.status.set_text("LintabOS is removed. Restart; Windows will check its disk once, which is normal."
                                 + ("\n\nNote:\n" + "\n".join(warnings) if warnings else ""))
            self.bar.set_fraction(1.0)
        return False


class RemoveApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.Remove")
        self.connect("activate", lambda app: RemoveWindow(app).present())


def main() -> int:
    import os
    if os.geteuid() != 0 and "--no-root-check" not in sys.argv:
        print("lintab-uninstall-gui must run as root; start it with lintab-uninstall-launch.", file=sys.stderr)
        return 1
    return RemoveApp().run([a for a in sys.argv if a != "--no-root-check"])


if __name__ == "__main__":
    sys.exit(main())
