# SPDX-License-Identifier: MIT
"""School mode: switch every camera off, in a way a student account can't simply undo.

Three independent layers, because no single one covers every camera:

1. **Drivers.** modprobe is told never to load the camera drivers (``uvcvideo`` for USB webcams, the Intel IPU6/IPU3
   drivers for the tablet's built-in cameras) and the loaded ones are unloaded.
2. **Permissions.** udev makes every ``/dev/video*`` and ``/dev/media*`` node unreadable by anyone, including root's
   desktop applications, and strips the per-user ``uaccess`` permission the desktop normally grants.
3. **USB.** Any USB device interface of the video class is de-authorized, so a webcam plugged in later stays dead
   even if its driver were somehow already loaded.

Turning school mode **on** needs no password. Turning it **off** needs an administrator's password (polkit), so a
standard-user student can't, while a parent or teacher can. ``lintab-school-mode verify`` checks the result.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

# Kernel drivers that expose cameras on the Lenovo IdeaPad Duet 3 and on USB webcams.
CAMERA_MODULES = ("uvcvideo", "intel_ipu6_isys", "intel_ipu6", "ipu3_cio2", "ipu3_imgu", "intel_ipu7_isys", "intel_ipu7")

HELPER_ON = "/usr/libexec/lintab/school-mode-on"
HELPER_OFF = "/usr/libexec/lintab/school-mode-off"


@dataclass
class Paths:
    root: str = "/"

    def at(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    @property
    def modprobe(self) -> str:
        return self.at("etc/modprobe.d/lintabos-school-mode.conf")

    @property
    def udev(self) -> str:
        return self.at("etc/udev/rules.d/60-lintabos-school-mode.rules")

    @property
    def flag(self) -> str:
        return self.at("etc/lintabos/school-mode")


def render_modprobe() -> str:
    lines = ["# LintabOS school mode: camera drivers must not load. Removed again by `lintab-school-mode off`."]
    for module in CAMERA_MODULES:
        lines += [f"blacklist {module}", f"install {module} /bin/false"]
    return "\n".join(lines) + "\n"


def render_udev() -> str:
    return (
        "# LintabOS school mode: no camera node may be opened. Removed again by `lintab-school-mode off`.\n"
        'SUBSYSTEM=="video4linux", MODE="0000", TAG-="uaccess", TAG-="seat"\n'
        'SUBSYSTEM=="media", MODE="0000", TAG-="uaccess", TAG-="seat"\n'
        'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface", ATTR{bInterfaceClass}=="0e", ATTR{authorized}="0"\n'
    )


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o644)


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def is_on(paths: Paths = Paths()) -> bool:
    return os.path.exists(paths.flag)


def turn_on(paths: Paths = Paths(), run: Callable[[list[str]], object] = _run) -> None:
    _write(paths.modprobe, render_modprobe())
    _write(paths.udev, render_udev())
    _write(paths.flag, "school mode is ON; turn it off with `lintab-school-mode off` (needs an administrator)\n")
    run(["udevadm", "control", "--reload"])
    # re-apply the rules to camera nodes that already exist
    run(["udevadm", "trigger", "--action=change", "--subsystem-match=video4linux", "--subsystem-match=media"])
    run(["udevadm", "trigger", "--action=add", "--subsystem-match=usb", "--attr-match=bInterfaceClass=0e"])
    for module in CAMERA_MODULES:
        run(["modprobe", "-r", module])
    run(["udevadm", "settle", "--timeout=5"])


def turn_off(paths: Paths = Paths(), run: Callable[[list[str]], object] = _run) -> None:
    for path in (paths.modprobe, paths.udev, paths.flag):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    run(["udevadm", "control", "--reload"])
    run(["udevadm", "trigger", "--action=change", "--subsystem-match=video4linux", "--subsystem-match=media"])
    run(["udevadm", "trigger", "--action=add", "--subsystem-match=usb", "--attr-match=bInterfaceClass=0e"])
    for module in ("uvcvideo", "intel_ipu6_isys", "ipu3_cio2"):
        run(["modprobe", module])


@dataclass
class Status:
    on: bool
    open_nodes: list[str]      # camera device nodes that someone could still open
    loaded_modules: list[str]  # camera drivers still loaded

    @property
    def effective(self) -> bool:
        """School mode is on AND nothing can reach a camera."""
        return self.on and not self.open_nodes and not self.loaded_modules


def inspect(paths: Paths = Paths(), dev_root: str = "/dev", proc_modules: str = "/proc/modules") -> Status:
    open_nodes = []
    for pattern in ("video*", "media*", "v4l-subdev*"):
        for node in sorted(glob.glob(os.path.join(dev_root, pattern))):
            try:
                mode = stat.S_IMODE(os.stat(node).st_mode)
            except OSError:
                continue
            if mode & 0o077 or mode & 0o700:
                open_nodes.append(node)
    loaded: list[str] = []
    try:
        with open(proc_modules) as f:
            names = {line.split()[0] for line in f if line.strip()}
        loaded = [m for m in CAMERA_MODULES if m in names]
    except OSError:
        pass
    return Status(is_on(paths), open_nodes, loaded)


def _elevate(helper: str, action: str) -> int:
    """Run the privileged helper through pkexec (polkit decides whether a password is needed)."""
    if os.geteuid() == 0:
        return subprocess.run([helper]).returncode
    if not shutil.which("pkexec"):
        print("pkexec is missing; run this as root", file=sys.stderr)
        return 1
    return subprocess.run(["pkexec", helper]).returncode


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-school-mode", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["status", "on", "off", "verify"], nargs="?", default="status")
    args = ap.parse_args(argv)

    if args.action == "on":
        return _elevate(HELPER_ON, "on")
    if args.action == "off":
        return _elevate(HELPER_OFF, "off")

    status = inspect()
    if args.action == "verify":
        if not status.on:
            print("school mode is OFF")
            return 1
        if status.effective:
            print("school mode is ON and no camera is reachable")
            return 0
        print("school mode is ON but a camera may still be reachable:", file=sys.stderr)
        for node in status.open_nodes:
            print(f"  openable: {node}", file=sys.stderr)
        for module in status.loaded_modules:
            print(f"  driver still loaded: {module} (it unloads on the next restart)", file=sys.stderr)
        return 2
    print(f"school mode: {'ON' if status.on else 'off'}")
    print(f"camera nodes openable: {len(status.open_nodes)}   camera drivers loaded: {len(status.loaded_modules)}")
    return 0


def helper_main(action: str) -> int:
    """Entry point of the two root helpers; their pkexec policies differ (on = no password, off = admin)."""
    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    (turn_on if action == "on" else turn_off)()
    status = inspect()
    print("school mode is " + ("ON" if status.on else "off"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
