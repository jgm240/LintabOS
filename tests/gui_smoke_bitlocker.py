# SPDX-License-Identifier: MIT
"""Headless GUI smoke test for the BitLocker step: disk page with an encrypted Windows, key page,
confirmation page and progress page, with the real decryptor replaced by a fake that reports progress."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from gi.repository import GLib, Gtk
from lintab import bitlocker, bitlocker_decrypt as bd, disks, gui, gui_bitlocker, plan as planmod
from lintab.disks import GiB, MiB

PASSWORD = "466895-217492-569250-069608-104434-135707-527241-083622"
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)


def fake_scan():
    d = disks.Disk(path="/dev/sda", size=128 * GiB, sector_size=4096, model="WDC SDINFDO4-128G", transport="",
                   removable=False, label="gpt", first_usable=MiB, last_usable=128 * GiB - MiB)
    off = MiB
    for n, size, guid, name, fs in (
            (1, 260 * MiB, disks.GUID_ESP, "EFI system partition", "vfat"),
            (2, 16 * MiB, disks.GUID_MS_RESERVED, "Microsoft reserved", ""),
            (3, 118 * GiB, disks.GUID_MS_BASIC_DATA, "Basic data partition", "BitLocker"),
            (4, 1 * GiB, disks.GUID_WIN_RE, "Recovery", "ntfs")):
        d.partitions.append(disks.Partition(n, f"/dev/sda{n}", off, size, guid, f"0000-{n}", name, fs))
        off += size
    return [gui.DiskScan(disk=d, windows=d.partitions[2], ntfs=None, bitlocker=True, esp=d.partitions[0],
                         windows_problem="Windows is encrypted with BitLocker")]


gui.scan_disks = fake_scan
bd.mount_esp = lambda dev, name="esp": "/tmp/fake-esp"
bd.unmount_esp = lambda mnt: None
bitlocker.lock_all = lambda *a, **k: None


def fake_preflight(device, key, root, handle=None):
    layout = bd.Layout(512, 118 * GiB, 8192, 0x8004, 4, "ab" * 16)
    return bd.Preflight(layout, bitlocker.VolumeInfo(118 * GiB, 31 * GiB, 31 * GiB, True), True, 100 * MiB, [], [], 9)


def fake_decrypt(device, key, root, progress=None, **kw):
    total = 118 * GiB
    for i in range(1, 11):
        progress("decrypt", total * i // 10, total, "Decrypting Windows")
        time.sleep(0.15)


bd.preflight = fake_preflight
bd.decrypt_in_place = fake_decrypt


def shot(name):
    subprocess.run(["import", "-window", "root", f"{OUT}/{name}.png"], check=False)
    print("shot", name, flush=True)


def main():
    app = gui.InstallerApp()
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)

    def on_activate(a):
        w = gui.InstallerWindow(a)
        w.set_default_size(900, 780)
        w.present()
        steps = [
            (1500, lambda: w._go_disks()),
            (3500, lambda: shot("bl1-disks")),
            (3600, lambda: w._bitlocker_clicked()),
            (4200, lambda: shot("bl2-key")),
            (4300, lambda: w.nav.get_visible_page().get_child()),
        ]
        state = {}

        def key_ok():
            step = [p for p in [w.nav.get_visible_page()]]
            # find the step object through the closure kept by the button: re-create for the test
            return None

        for delay, fn in steps:
            GLib.timeout_add(delay, lambda f=fn: (f(), False)[1])

        def go_confirm():
            # drive the real step object (created by _bitlocker_clicked) through its own methods
            step = w._bl_step
            step.key_row.set_text(PASSWORD)
            step._check_clicked()
        GLib.timeout_add(4400, lambda: (go_confirm(), False)[1])
        GLib.timeout_add(7000, lambda: (shot("bl3-confirm"), False)[1])

        def confirm():
            step = w._bl_step
            step.understand.set_active(True)
            step.typed.set_text("DECRYPT")
        GLib.timeout_add(7100, lambda: (confirm(), False)[1])
        GLib.timeout_add(7550, lambda: (print("go_button sensitive after confirming:", w._bl_step.go_button.get_sensitive(), flush=True), False)[1])
        GLib.timeout_add(7560, lambda: (w._bl_step.typed.set_text("decrypt"), False)[1])
        GLib.timeout_add(7570, lambda: (print("go_button sensitive with wrong text:", w._bl_step.go_button.get_sensitive(), flush=True), False)[1])
        GLib.timeout_add(7580, lambda: (w._bl_step.typed.set_text("DECRYPT"), False)[1])
        GLib.timeout_add(7600, lambda: (shot("bl4-confirm-ready"), False)[1])
        GLib.timeout_add(7700, lambda: (w._bl_step._confirm_clicked(), False)[1])
        GLib.timeout_add(8300, lambda: (shot("bl5-progress"), False)[1])
        GLib.timeout_add(10500, lambda: (shot("bl6-done"), False)[1])
        GLib.timeout_add(10800, lambda: (a.quit(), False)[1])

    app.connect("activate", on_activate)
    app.run([])



PASSWORD = "466895-217492-569250-069608-104434-135707-527241-083622"
main()
