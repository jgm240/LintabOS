# SPDX-License-Identifier: MIT
"""GTK4 / libadwaita installer for LintabOS.

Touch-first: large targets, one decision per page. All disk work is delegated to
:mod:`lintab.plan` (what to do to the disk) and :mod:`lintab.install` (copy the
system and set up GRUB); this file is only the wizard around them.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import bitlocker, bitlocker_decrypt, disks, install as installmod, plan as planmod  # noqa: E402
from .gui_bitlocker import BitLockerStep  # noqa: E402
from .disks import GiB  # noqa: E402

APP_ID = "org.lintabos.Installer"
MODE_DUALBOOT, MODE_FREE, MODE_WIPE = "dualboot", "free", "wipe"


@dataclass
class DiskScan:
    disk: disks.Disk
    windows: Optional[disks.Partition] = None
    ntfs: Optional[planmod.NtfsInfo] = None
    windows_problem: str = ""  # why Windows can't be shrunk yet (user-facing)
    free: list = field(default_factory=list)
    bitlocker: bool = False  # the Windows partition is BitLocker-encrypted
    pending: Optional[dict] = None  # an interrupted BitLocker decryption (its journal state), if any
    esp: Optional[disks.Partition] = None

    @property
    def max_linux(self) -> int:
        return planmod.max_linux_size(self.windows, self.ntfs) if self.windows and self.ntfs else 0


def _pending_decrypt(esp: Optional[disks.Partition]) -> Optional[dict]:
    """An interrupted BitLocker decryption left a journal on this disk's EFI partition?"""
    if esp is None:
        return None
    import tempfile
    mnt = tempfile.mkdtemp(prefix="lintab-esp-scan-")
    try:
        if disks.run(["mount", "-o", "ro", esp.path, mnt], check=False).returncode != 0:
            return None
        try:
            found = bitlocker_decrypt.find_pending([mnt])
        finally:
            disks.run(["umount", mnt], check=False)
        return found[0] if found else None
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass


def scan_disks() -> list[DiskScan]:
    scans = []
    for d in disks.list_disks():
        s = DiskScan(disk=d, free=[r for r in d.free_regions() if r.size >= planmod.MIN_LINUX_SIZE])
        if d.label == "gpt":
            s.windows = disks.find_windows(d)
            s.esp = d.find_esp()
            s.pending = _pending_decrypt(s.esp)
            s.bitlocker = bool(s.windows and disks.is_bitlocker(s.windows))
            if s.windows:
                try:
                    s.ntfs = planmod.probe_ntfs(s.windows)
                except planmod.PlanError as exc:
                    s.windows_problem = str(exc)
        scans.append(s)
    return scans


class PartitionBar(Gtk.DrawingArea):
    """Shows Windows vs LintabOS as two coloured blocks that follow the slider."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_height(56)
        self.set_hexpand(True)
        self.fractions: list[tuple[float, tuple[float, float, float], str]] = []
        self.set_draw_func(self._draw)

    def set_layout(self, parts: list[tuple[float, tuple[float, float, float], str]]) -> None:
        self.fractions = parts
        self.queue_draw()

    def _draw(self, _area, cr, width, height) -> None:
        x = 0.0
        total = sum(f for f, _, _ in self.fractions) or 1.0
        for frac, (r, g, b), label in self.fractions:
            w = width * frac / total
            cr.set_source_rgb(r, g, b)
            cr.rectangle(x, 0, max(w - 2, 1), height)
            cr.fill()
            if w > 90:
                cr.set_source_rgb(1, 1, 1)
                cr.select_font_face("Sans")
                cr.set_font_size(15)
                cr.move_to(x + 12, height / 2 + 5)
                cr.show_text(label)
            x += w


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Install LintabOS", default_width=900, default_height=700)
        self.scans: list[DiskScan] = []
        self.mode = MODE_WIPE
        self.linux_gb = 40.0
        self.plan: Optional[planmod.Plan] = None

        self.nav = Adw.NavigationView()
        self.toasts = Adw.ToastOverlay(child=self.nav)
        self.set_content(self.toasts)
        self.nav.add(self._welcome_page())

    # ---------------------------------------------------------------- helpers
    def _page(self, title: str, tag: str, body: Gtk.Widget, footer: Optional[Gtk.Widget] = None,
              can_pop: bool = True) -> Adw.NavigationPage:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_back_button(can_pop)
        view.add_top_bar(header)
        clamp = Adw.Clamp(maximum_size=640, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18, child=body)
        scroll = Gtk.ScrolledWindow(child=clamp, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        view.set_content(scroll)
        if footer is not None:
            view.add_bottom_bar(footer)
        return Adw.NavigationPage(child=view, title=title, tag=tag)

    def _footer(self, label: str, callback, style: str = "suggested-action") -> Gtk.Widget:
        button = Gtk.Button(label=label, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12)
        button.add_css_class("pill")
        button.add_css_class(style)
        button.connect("clicked", lambda _b: callback())
        return button

    def _error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    # ---------------------------------------------------------------- welcome
    def _welcome_page(self) -> Adw.NavigationPage:
        status = Adw.StatusPage(
            title="Welcome to LintabOS",
            description="Debian and GNOME, tuned for the Lenovo IdeaPad Duet 3.\n"
                        "Install it next to Windows or on its own. Nothing is changed until the last step.")
        for candidate in ("/usr/share/lintabos/logo-256.png",
                          os.path.join(os.path.dirname(__file__), "..", "..", "live", "config", "includes.chroot",
                                  "usr", "share", "lintabos", "logo-256.png")):  # running from the source tree
            if os.path.exists(candidate):
                status.set_paintable(Gdk.Texture.new_from_filename(candidate))
                break
        else:
            status.set_icon_name("computer-symbolic")
        return self._page("Welcome", "welcome", status,
                          self._footer("Install LintabOS", self._go_disks), can_pop=False)

    # ------------------------------------------------------------------ disks
    def _go_disks(self) -> None:
        spinner_page = Adw.StatusPage(title="Looking at your disks…", description="This takes a few seconds.")
        spinner_page.set_child(Gtk.Spinner(spinning=True, width_request=48, height_request=48))
        page = self._page("Disks", "scanning", spinner_page)
        self.nav.push(page)

        def work() -> None:
            try:
                scans = scan_disks()
                GLib.idle_add(self._scan_done, scans, None)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                GLib.idle_add(self._scan_done, [], str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, scans: list[DiskScan], error: Optional[str]) -> None:
        self.nav.pop()  # spinner page
        if error or not scans:
            self._error("No usable disk found", error or
                        "LintabOS needs an internal disk of at least 16 GB (the USB stick you booted from is hidden).")
            return
        self.scans = scans
        self.nav.push(self._disk_page())

    def _disk_page(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        names = Gtk.StringList()
        for s in self.scans:
            names.append(s.disk.display_name)
        self.disk_row = Adw.ComboRow(title="Install on", model=names)
        self.disk_row.connect("notify::selected", lambda *_: self._refresh_mode_rows())
        group = Adw.PreferencesGroup()
        group.add(self.disk_row)
        box.append(group)

        self.banner = Adw.Banner(title="", revealed=False)
        self._banner_handler = None
        box.append(self.banner)

        self.bl_group = Adw.PreferencesGroup()
        self.bl_row = Adw.ActionRow(title="Windows is encrypted with BitLocker", title_lines=2, subtitle_lines=4)
        self.bl_button = Gtk.Button(label="Turn off BitLocker…", valign=Gtk.Align.CENTER)
        self.bl_button.add_css_class("suggested-action")
        self.bl_button.connect("clicked", lambda _b: self._bitlocker_clicked())
        self.bl_row.add_suffix(self.bl_button)
        self.bl_group.add(self.bl_row)
        box.append(self.bl_group)

        self.mode_group = Adw.PreferencesGroup(title="How do you want to install?")
        self.rb_dual = Gtk.CheckButton()
        self.rb_free = Gtk.CheckButton(group=self.rb_dual)
        self.rb_wipe = Gtk.CheckButton(group=self.rb_dual)
        self.row_dual = Adw.ActionRow(title="Install alongside Windows", activatable_widget=self.rb_dual)
        self.row_free = Adw.ActionRow(title="Use the free space", activatable_widget=self.rb_free)
        self.row_wipe = Adw.ActionRow(title="Erase the disk and install LintabOS", activatable_widget=self.rb_wipe)
        for row, rb in ((self.row_dual, self.rb_dual), (self.row_free, self.rb_free), (self.row_wipe, self.rb_wipe)):
            row.add_prefix(rb)
            self.mode_group.add(row)
            rb.connect("toggled", lambda *_: self._mode_changed())
        box.append(self.mode_group)

        self.size_group = Adw.PreferencesGroup(title="Space for LintabOS")
        self.bar = PartitionBar()
        self.size_label = Gtk.Label(xalign=0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 12, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.connect("value-changed", lambda s: self._size_changed(s.get_value()))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for w in (self.bar, self.scale, self.size_label):
            inner.append(w)
        self.size_group.add(inner)
        box.append(self.size_group)

        self._refresh_mode_rows()
        return self._page("Where to install", "disks", box, self._footer("Continue", self._go_account))

    def _bitlocker_clicked(self) -> None:
        s = self.scan
        if s.windows is None:
            self._error("Windows not found", "The Windows partition could not be identified.")
            return
        self._bl_step = BitLockerStep(self, s.windows, s.esp, s.pending, self._after_bitlocker)
        self._bl_step.start()

    def _after_bitlocker(self) -> None:
        """BitLocker is off: go back to the start of the disk choice and look at the disks again."""
        self.nav.pop_to_tag("welcome")
        self._go_disks()

    @property
    def scan(self) -> DiskScan:
        return self.scans[self.disk_row.get_selected()]

    def _refresh_mode_rows(self) -> None:
        s = self.scan
        can_dual = bool(s.windows and s.ntfs)
        self.row_dual.set_visible(s.windows is not None)
        self.row_dual.set_sensitive(can_dual)
        self.row_dual.set_subtitle(
            f"Windows stays intact. LintabOS can take up to {s.max_linux / GiB:.0f} GB from it."
            if can_dual else ("Turn off BitLocker first (see above)." if s.bitlocker
                              else "Windows needs a small change before it can be shrunk (see above)."))
        self.row_free.set_visible(bool(s.free))
        if s.free:
            biggest = max(r.size for r in s.free)
            self.row_free.set_subtitle(f"{biggest / GiB:.0f} GB of unused space on this disk.")
        self.row_wipe.set_subtitle("Everything on this disk is deleted, including Windows and all files.")

        show_bl = s.bitlocker or s.pending is not None
        self.bl_group.set_visible(show_bl)
        if s.pending is not None:
            self.bl_row.set_title("A BitLocker decryption was interrupted")
            self.bl_row.set_subtitle("Finish it before doing anything else with this disk. Nothing is lost.")
            self.bl_button.set_label("Finish decrypting…")
        elif s.bitlocker:
            self.bl_row.set_title("Windows is encrypted with BitLocker")
            self.bl_row.set_subtitle("An encrypted drive can't be shrunk safely, so BitLocker has to be turned "
                                     "off first. You'll see the safest way, or you can do it from here with your recovery key.")
            self.bl_button.set_label("Turn off BitLocker…")

        # The BitLocker group already explains the BitLocker case; the banner covers other Windows problems.
        problem = "" if show_bl else s.windows_problem
        self.banner.set_revealed(bool(problem))
        self.banner.set_title(problem.split("\n")[0] if problem else "")
        self.banner.set_button_label("Details" if problem else None)
        if self._banner_handler is not None:
            self.banner.disconnect(self._banner_handler)
            self._banner_handler = None
        if problem:
            self._banner_handler = self.banner.connect(
                "button-clicked", lambda _b: self._error("Prepare Windows first", problem))

        if can_dual:
            self.rb_dual.set_active(True)
        elif s.free:
            self.rb_free.set_active(True)
        else:
            self.rb_wipe.set_active(True)
        self._mode_changed()

    def _mode_changed(self) -> None:
        s = self.scan
        self.mode = MODE_DUALBOOT if self.rb_dual.get_active() else MODE_FREE if self.rb_free.get_active() else MODE_WIPE
        show_slider = self.mode in (MODE_DUALBOOT, MODE_FREE)
        self.size_group.set_visible(show_slider)
        if self.mode == MODE_DUALBOOT and s.windows and s.ntfs:
            hi = max(planmod.MIN_LINUX_SIZE // GiB + 1, s.max_linux // GiB)
            self.scale.set_range(planmod.MIN_LINUX_SIZE // GiB, hi)
            self.scale.set_value(min(max(self.linux_gb, planmod.MIN_LINUX_SIZE // GiB), hi, 64))
        elif self.mode == MODE_FREE and s.free:
            hi = max(r.size for r in s.free) // GiB
            self.scale.set_range(planmod.MIN_LINUX_SIZE // GiB, max(hi, planmod.MIN_LINUX_SIZE // GiB + 1))
            self.scale.set_value(hi)
        self._size_changed(self.scale.get_value())

    def _size_changed(self, gb: float) -> None:
        self.linux_gb = gb
        s = self.scan
        if self.mode == MODE_DUALBOOT and s.windows:
            win_after = s.windows.size / GiB - gb
            self.bar.set_layout([(win_after, (0.16, 0.42, 0.78), f"Windows {win_after:.0f} GB"),
                                 (gb, (0.21, 0.82, 0.73), f"LintabOS {gb:.0f} GB")])
            self.size_label.set_text(f"Windows keeps {win_after:.0f} GB, LintabOS gets {gb:.0f} GB. "
                                     f"Windows will check its disk once the next time it starts; that's normal.")
        elif self.mode == MODE_FREE:
            self.bar.set_layout([(gb, (0.21, 0.82, 0.73), f"LintabOS {gb:.0f} GB")])
            self.size_label.set_text(f"LintabOS gets {gb:.0f} GB of free space.")

    # ---------------------------------------------------------------- account
    def _go_account(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="About you")
        self.e_name = Adw.EntryRow(title="Full name")
        self.e_user = Adw.EntryRow(title="User name")
        self.e_pass = Adw.PasswordEntryRow(title="Password")
        self.e_pass2 = Adw.PasswordEntryRow(title="Repeat password")
        self.e_host = Adw.EntryRow(title="Computer name")
        self.e_host.set_text("lintab")
        self.e_tz = Adw.EntryRow(title="Time zone (Region/City)")
        self.e_tz.set_text(_current_timezone())
        self.s_auto = Adw.SwitchRow(title="Log in automatically", subtitle="Skips the password at startup.")
        self._user_edited = False
        self.e_name.connect("changed", lambda r: self._suggest_user(r.get_text()))
        self.e_user.connect("changed", lambda _r: self._on_user_changed())
        for w in (self.e_name, self.e_user, self.e_pass, self.e_pass2, self.e_host, self.e_tz, self.s_auto):
            group.add(w)
        box.append(group)
        self.nav.push(self._page("Your account", "account", box, self._footer("Continue", self._go_summary)))

    def _suggest_user(self, full: str) -> None:
        if not self._user_edited:
            self._suggesting = True
            self.e_user.set_text("".join(c for c in full.lower().split(" ")[0] if c.isalnum()))
            self._suggesting = False

    def _on_user_changed(self) -> None:
        if not getattr(self, "_suggesting", False):
            self._user_edited = True

    # ---------------------------------------------------------------- summary
    def _build_plan(self) -> planmod.Plan:
        s = self.scan
        if self.mode == MODE_DUALBOOT:
            return planmod.plan_dualboot(s.disk, int(self.linux_gb * GiB), s.windows, s.ntfs)
        if self.mode == MODE_FREE:
            region = max(s.free, key=lambda r: r.size)
            return planmod.plan_free_space(s.disk, region, int(self.linux_gb * GiB))
        return planmod.plan_wipe(s.disk)

    def _go_summary(self) -> None:
        cfg_error = None
        try:
            if self.e_pass.get_text() != self.e_pass2.get_text():
                raise installmod.InstallError("The two passwords don't match.")
            self.cfg = installmod.InstallConfig(
                plan=planmod.Plan(disk="", mode=""),
                fullname=self.e_name.get_text().strip() or self.e_user.get_text(),
                username=self.e_user.get_text().strip(),
                password=self.e_pass.get_text(),
                hostname=self.e_host.get_text().strip() or "lintab",
                timezone=self.e_tz.get_text().strip() or "UTC",
                autologin=self.s_auto.get_active())
            self.cfg.validate()
            self.plan = self._build_plan()
        except (installmod.InstallError, planmod.PlanError) as exc:
            cfg_error = str(exc)
        if cfg_error:
            self._error("Can't continue", cfg_error)
            return
        self.cfg.plan = self.plan

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="Ready to install", description=f"Disk: {self.scan.disk.display_name}")
        for line in self.plan.summary:
            group.add(Adw.ActionRow(title=line, title_lines=3))
        box.append(group)
        self.confirm = None
        destructive = self.mode == MODE_WIPE
        if destructive:
            self.confirm = Adw.SwitchRow(title="I understand everything on this disk will be erased")
            g2 = Adw.PreferencesGroup()
            g2.add(self.confirm)
            box.append(g2)
        footer = self._footer("Install now", self._confirm_and_install, "destructive-action" if destructive else "suggested-action")
        self.nav.push(self._page("Summary", "summary", box, footer))

    def _confirm_and_install(self) -> None:
        if self.confirm is not None and not self.confirm.get_active():
            self._error("Please confirm", "Turn on the switch to confirm that the disk may be erased.")
            return
        self._start_install()

    # --------------------------------------------------------------- progress
    def _start_install(self) -> None:
        self.progress_bar = Gtk.ProgressBar(show_text=False, margin_top=24)
        self.progress_label = Gtk.Label(label="Starting…")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.append(self.progress_bar)
        inner.append(self.progress_label)
        status = Adw.StatusPage(title="Installing LintabOS", description="Please keep the tablet plugged in.")
        status.set_child(inner)
        self.nav.push(self._page("Installing", "progress", status, can_pop=False))

        def set_progress(frac: float, msg: str) -> None:
            GLib.idle_add(self._set_progress, frac, msg)

        def work() -> None:
            try:
                planmod.apply_plan(self.plan, progress=lambda i, n, d: set_progress(0.10 * i / n, d))
                installmod.install(self.cfg, lambda f, m: set_progress(0.10 + 0.90 * f, m))
                GLib.idle_add(self._install_done, None)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                GLib.idle_add(self._install_done, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _set_progress(self, frac: float, msg: str) -> bool:
        self.progress_bar.set_fraction(min(max(frac, 0.0), 1.0))
        self.progress_label.set_text(msg)
        return False

    def _install_done(self, error: Optional[str]) -> bool:
        if error:
            status = Adw.StatusPage(title="Installation failed", icon_name="dialog-error-symbolic", description=error)
            status.add_css_class("compact")
            self.nav.push(self._page("Failed", "failed", status, self._footer("Close", self.close, "destructive-action"),
                                     can_pop=False))
        else:
            status = Adw.StatusPage(title="LintabOS is installed", icon_name="emblem-ok-symbolic",
                                    description="Remove the USB stick and restart. In the boot menu, choose "
                                                "LintabOS, or “Boot into Windows” to go back to Windows.")
            self.nav.push(self._page("Done", "done", status, self._footer("Restart now", _reboot), can_pop=False))
        return False


def _current_timezone() -> str:
    try:
        return os.path.realpath("/etc/localtime").split("/zoneinfo/", 1)[1]
    except (IndexError, OSError):
        return "UTC"


def _reboot() -> None:
    os.system("systemctl reboot")


class InstallerApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.connect("activate", lambda app: InstallerWindow(app).present())


def main() -> int:
    if os.geteuid() != 0 and "--no-root-check" not in sys.argv:
        print("lintab-installer must run as root; start it with lintab-installer-launch.", file=sys.stderr)
        return 1
    argv = [a for a in sys.argv if a != "--no-root-check"]
    return InstallerApp().run(argv)


if __name__ == "__main__":
    sys.exit(main())
