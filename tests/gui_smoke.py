# SPDX-License-Identifier: MIT
"""Headless GUI smoke test: drives the installer wizard over a synthetic
128 GB Windows tablet disk and screenshots each page. Run via
scripts/test-gui.sh (needs Xvfb)."""
import os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from gi.repository import GLib
from lintab import disks, gui, plan as planmod
from lintab.disks import GiB, MiB

OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)

def fake_disk():
    d = disks.Disk(path="/dev/sda", size=128 * GiB, sector_size=4096, model="WDC SDINFDO4-128G",
                   transport="", removable=False, label="gpt", first_usable=MiB, last_usable=128 * GiB - MiB)
    off = MiB
    def add(n, size, guid, name, fs):
        nonlocal off
        d.partitions.append(disks.Partition(n, f"/dev/sda{n}", off, size, guid, f"00000000-0000-0000-0000-00000000000{n}", name, fs))
        off += size
    add(1, 260 * MiB, disks.GUID_ESP, "EFI system partition", "vfat")
    add(2, 16 * MiB, disks.GUID_MS_RESERVED, "Microsoft reserved", "")
    add(3, 118 * GiB, disks.GUID_MS_BASIC_DATA, "Basic data partition", "ntfs")
    add(4, 1 * GiB, disks.GUID_WIN_RE, "Recovery", "ntfs")
    return d

def fake_scan():
    d = fake_disk()
    win = d.partitions[2]
    return [gui.DiskScan(disk=d, windows=win, ntfs=planmod.NtfsInfo(size=win.size, min_size=31 * GiB), free=[])]

gui.scan_disks = fake_scan
planmod.esp_has_windows = lambda esp: True
planmod.esp_free_bytes = lambda esp: 200 * MiB

def shot(name):
    subprocess.run(["import", "-window", "root", f"{OUT}/{name}.png"], check=False)
    print("shot", name, flush=True)

def main():
    app = gui.InstallerApp()
    from gi.repository import Gtk
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    state = {}
    def on_activate(a):
        w = gui.InstallerWindow(a)
        w.set_default_size(900, 760)
        w.present()
        state["w"] = w
        steps = [
            (1500, lambda: shot("1-welcome")),
            (1800, lambda: w._go_disks()),
            (3000, lambda: (shot("2-disks"), print("mode:", w.mode, "gb:", w.linux_gb, "max:", w.scan.max_linux / GiB))),
            (3100, lambda: w.scale.set_value(50)),
            (3700, lambda: shot("3-disks-50gb")),
            (3800, lambda: w._go_account()),
            (4200, lambda: (w.e_name.set_text("Ada Lovelace"), w.e_pass.set_text("x"), w.e_pass2.set_text("x"))),
            (4600, lambda: shot("4-account")),
            (4700, lambda: w._go_summary()),
            (5300, lambda: (shot("5-summary"), print(w.plan.describe()))),
            (5400, lambda: w.rb_wipe.set_active(True)),
            (5900, lambda: (w._mode_changed(), shot("6-wipe-mode"))),
            (6300, lambda: a.quit()),
        ]
        for delay, fn in steps:
            GLib.timeout_add(delay, lambda f=fn: (f(), False)[1])
    app.connect("activate", on_activate)
    app.run([])

main()
