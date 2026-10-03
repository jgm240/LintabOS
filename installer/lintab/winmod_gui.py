# SPDX-License-Identifier: MIT
"""LinWinMod: edit the Windows files, and browse (read-only) the Windows registry."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import bitlocker, disks, winfiles, winmod  # noqa: E402


class WinModWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="LinWinMod", default_width=760, default_height=640)
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_content(view)

        files_page = FilesPage()
        self.stack.add_titled(files_page, "files", "Files").set_icon_name("folder-symbolic")
        registry_page = RegistryPage()
        self.stack.add_titled(registry_page, "registry", "Registry").set_icon_name("edit-find-symbolic")


# ------------------------------------------------------------------------------- Files --

class FilesPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                         margin_start=18, margin_end=18)
        self.status = Gtk.Label(label="Looking for Windows…", wrap=True, xalign=0)
        self.append(self.status)
        self.button = Gtk.Button(label="Open Windows files", halign=Gtk.Align.CENTER)
        self.button.add_css_class("suggested-action")
        self.button.add_css_class("pill")
        self.button.connect("clicked", lambda _b: self._open())
        self.button.set_sensitive(False)
        self.append(self.button)
        self.append(Gtk.Label(
            label="Opens your Windows drive, read-write, in Files. Refused if Windows left the drive unclean or "
                  "hibernated (Fast Startup) — boot Windows and shut it down fully first.",
            wrap=True, xalign=0, css_classes=["dim-label"]))

        self.picker_group = Adw.PreferencesGroup(
            title="Examine a different partition", visible=False,
            description="If the automatic search above didn't find your Windows drive — an unusual layout, a "
                        "second data partition, or one whose filesystem wasn't recognised as NTFS — pick any "
                        "Windows-type partition on this tablet by hand.")
        picker_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=6, margin_bottom=6,
                            margin_start=12, margin_end=12)
        self.ntfs_picker = Gtk.DropDown(model=Gtk.StringList(), hexpand=True)
        picker_row.append(self.ntfs_picker)
        self.use_button = Gtk.Button(label="Use this partition")
        self.use_button.connect("clicked", lambda _b: self._use_picked())
        picker_row.append(self.use_button)
        self.picker_group.add(picker_row)
        self.append(self.picker_group)

        self._partition = None
        self._ntfs_partitions: list = []
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self) -> None:
        try:
            parts = winfiles.windows_partitions()
            ntfs = winfiles.list_microsoft_data_partitions()
        except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the page is stuck on "Looking…" forever
            GLib.idle_add(self._scanned, None, [], str(exc))
            return
        GLib.idle_add(self._scanned, parts, ntfs, None)

    def _scanned(self, parts: Optional[list], ntfs: list, error: Optional[str]) -> bool:
        self._ntfs_partitions = ntfs
        if ntfs:
            model = self.ntfs_picker.get_model()
            for part in ntfs:
                model.append(f"{part.path}  ({part.size / (1024**3):.0f} GB)")
            self.picker_group.set_visible(True)
        if error:
            self.status.set_text(f"Could not look at the disks: {error}")
            return False
        if not parts:
            try:
                encrypted = any(disks.is_bitlocker(p) for d in disks.list_disks() for p in d.partitions if p.is_ms_data)
            except Exception:  # noqa: BLE001 - a failed extra check must not hide the real "no Windows found" message
                encrypted = False
            self.status.set_text(
                "This Windows drive is BitLocker-encrypted. Use “Unlock BitLocker Drive” first." if encrypted
                else "No Windows partition was found automatically on this tablet."
                     + (" Pick one below." if ntfs else ""))
            return False
        self._partition = max(parts, key=lambda p: p.size)
        self.status.set_text(f"Windows drive: {self._partition.path}")
        self.button.set_sensitive(True)
        return False

    def _use_picked(self) -> None:
        index = self.ntfs_picker.get_selected()
        if index < 0 or index >= len(self._ntfs_partitions):
            return
        chosen = self._ntfs_partitions[index]
        self.use_button.set_sensitive(False)
        self.button.set_sensitive(False)
        self.status.set_text(f"Setting up {chosen.path}…")

        def work() -> None:
            try:
                proc = subprocess.run(["pkexec", "lintab-windows-files", "enable", "--read-write",
                                       "--partition", chosen.path], capture_output=True, text=True)
            except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the page is stuck "Setting up…" forever
                GLib.idle_add(self._picked_set_up, chosen, 1, str(exc))
                return
            GLib.idle_add(self._picked_set_up, chosen, proc.returncode,
                         (proc.stderr or proc.stdout or "").strip())

        threading.Thread(target=work, daemon=True).start()

    def _picked_set_up(self, chosen, rc: int, message: str) -> bool:
        self.use_button.set_sensitive(True)
        if rc != 0:
            self.status.set_text(message.replace("error: ", "") or "That didn't work (cancelled, or not allowed).")
            return False
        self._partition = chosen
        self.status.set_text(f"Opening {chosen.path}…")

        def work() -> None:
            try:
                proc = winmod.ensure_writable()
            except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the page is stuck "Opening…" forever
                GLib.idle_add(self._mounted, 1, str(exc))
                return
            GLib.idle_add(self._mounted, proc.returncode, (proc.stderr or proc.stdout or "").strip())

        threading.Thread(target=work, daemon=True).start()
        return False

    def _open(self) -> None:
        self.button.set_sensitive(False)
        self.status.set_text("Checking the drive…")

        def work() -> None:
            try:
                info = bitlocker.probe(self._partition.path)
            except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the page is stuck "Checking…" forever
                GLib.idle_add(self._checked, None, str(exc))
                return
            GLib.idle_add(self._checked, info, None)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, info, error: Optional[str] = None) -> bool:
        if error:
            self.status.set_text(f"Could not check the drive: {error}")
            self.button.set_sensitive(True)
            return False
        if not info.healthy:
            messages = {
                "dirty": "Windows flagged this drive for a consistency check. Boot Windows, let it check the disk, "
                        "then shut down fully and try again.",
                "hibernated": "Windows is hibernated (Fast Startup). Boot Windows, run “powercfg /h off”, shut down "
                             "fully (hold Shift while clicking Shut down), then try again.",
            }
            self.status.set_text(messages.get(info.problem, info.problem or "This drive isn't safe to write to right now."))
            self.button.set_sensitive(True)
            return False
        self.status.set_text("Opening…")

        def work() -> None:
            try:
                proc = winmod.ensure_writable()
            except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or the page is stuck "Opening…" forever
                GLib.idle_add(self._mounted, 1, str(exc))
                return
            GLib.idle_add(self._mounted, proc.returncode, (proc.stderr or proc.stdout or "").strip())

        threading.Thread(target=work, daemon=True).start()
        return False

    def _mounted(self, rc: int, message: str) -> bool:
        self.button.set_sensitive(True)
        if rc != 0:
            self.status.set_text(message or "That didn't work (cancelled, or not allowed).")
            return False
        winmod.open_file_manager(winmod.MOUNT_POINT)
        self.status.set_text(f"Opened {winmod.MOUNT_POINT} for editing. It goes back to read-only on its own after use.")
        return False


# ----------------------------------------------------------------------------- Registry --

class RegistryPage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                         margin_start=18, margin_end=18)
        self.handle = None
        self.hive_label = ""
        self.path: list[str] = []

        banner = Adw.Banner(title="Read-only. LinWinMod never writes to the registry — SAM and SECURITY are never opened.",
                            revealed=True)
        self.append(banner)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.hive_picker = Gtk.DropDown(model=Gtk.StringList())
        self.hive_picker.connect("notify::selected", lambda *_a: self._pick_hive())
        top.append(self.hive_picker)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search key names…", hexpand=True)
        self.search_entry.connect("activate", lambda _e: self._search())
        top.append(self.search_entry)
        self.append(top)

        self.breadcrumb = Gtk.Label(label="", wrap=True, xalign=0, css_classes=["dim-label"])
        self.append(self.breadcrumb)
        self.up_button = Gtk.Button(label="Up", halign=Gtk.Align.START)
        self.up_button.connect("clicked", lambda _b: self._up())
        self.up_button.set_sensitive(False)
        self.append(self.up_button)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroller.set_child(self.list_box)
        self.append(scroller)

        self._hives: list = []
        threading.Thread(target=self._scan_hives, daemon=True).start()

    # -- hive discovery --
    def _scan_hives(self) -> None:
        try:
            hives = winmod.find_hives(winmod.MOUNT_POINT) if winmod.mount_status()[0] else []
        except Exception as exc:  # noqa: BLE001 - must still reach idle_add, or this page never finishes loading
            GLib.idle_add(self._hives_found, [], str(exc))
            return
        GLib.idle_add(self._hives_found, hives, None)

    def _hives_found(self, hives: list, error: Optional[str] = None) -> bool:
        if error:
            self.breadcrumb.set_text(f"Could not look for registry hives: {error}")
            return False
        self._hives = hives
        model = self.hive_picker.get_model()
        for hive in hives:
            model.append(hive.label)
        if not hives:
            self.breadcrumb.set_text("No readable hives found. Open a Windows drive from the Files tab first "
                                     "(SAM and SECURITY would not be listed here even if present).")
        elif len(hives) >= 1:
            self.hive_picker.set_selected(0)
        return False

    def _pick_hive(self) -> None:
        index = self.hive_picker.get_selected()
        if index < 0 or index >= len(self._hives):
            return
        hive = self._hives[index]
        try:
            self.handle = winmod.open_hive(hive.path)
        except winmod.BlockedHiveError as exc:
            self.handle = None
            self.breadcrumb.set_text(str(exc))
            self._render([])
            return
        except Exception as exc:  # noqa: BLE001 - a malformed hive must not crash the browser
            self.handle = None
            self.breadcrumb.set_text(f"Could not open {hive.label}: {exc}")
            self._render([])
            return
        self.hive_label = hive.label
        self.path = []
        self._refresh()

    # -- navigation --
    def _refresh(self) -> None:
        if self.handle is None:
            return
        try:
            node = winmod.navigate(self.handle, self.path)
            data = winmod.read_node(self.handle, node)
        except KeyError as missing:
            self.breadcrumb.set_text(f"“{missing}” no longer exists here.")
            self.path = self.path[:-1]
            return
        shown = "\\".join([self.hive_label, *self.path]) if self.path else self.hive_label
        self.breadcrumb.set_text(shown)
        self.up_button.set_sensitive(bool(self.path))
        self._render(data.child_names, data.values)

    def _descend(self, name: str) -> None:
        self.path.append(name)
        self._refresh()

    def _up(self) -> None:
        if self.path:
            self.path.pop()
            self._refresh()

    def _search(self) -> None:
        if self.handle is None:
            return
        needle = self.search_entry.get_text().strip()
        if not needle:
            return
        matches = winmod.search_keys(self.handle, needle)
        self.breadcrumb.set_text(f"{len(matches)} match(es) for “{needle}” in {self.hive_label}")
        self.up_button.set_sensitive(bool(self.path))
        self._render_search(matches)

    # -- rendering --
    def _clear(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt

    def _render(self, child_names: list[str] = (), values: list = ()) -> None:
        self._clear()
        if child_names:
            group = Adw.PreferencesGroup(title="Keys")
            for name in child_names:
                row = Adw.ActionRow(title=name, activatable=True)
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.connect("activated", lambda _r, n=name: self._descend(n))
                group.add(row)
            self.list_box.append(group)
        if values:
            group = Adw.PreferencesGroup(title="Values")
            for value in values:
                row = Adw.ActionRow(title=value.name or "(default)", subtitle=f"{value.type_name}: {value.display}",
                                    subtitle_lines=4)
                group.add(row)
            self.list_box.append(group)
        if not child_names and not values:
            self.list_box.append(Gtk.Label(label="(nothing here)", xalign=0, css_classes=["dim-label"]))

    def _render_search(self, matches: list[str]) -> None:
        self._clear()
        group = Adw.PreferencesGroup()
        for path in matches:
            row = Adw.ActionRow(title=path, activatable=True)
            row.connect("activated", lambda _r, p=path: self._goto(p))
            group.add(row)
        self.list_box.append(group)
        if not matches:
            self.list_box.append(Gtk.Label(label="No matches.", xalign=0, css_classes=["dim-label"]))

    def _goto(self, path: str) -> None:
        self.path = path.split("\\")
        self._refresh()


class WinModApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lintabos.WinMod")
        self.connect("activate", lambda app: WinModWindow(app).present())


def main() -> int:
    return WinModApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
