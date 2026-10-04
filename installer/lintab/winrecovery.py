# SPDX-License-Identifier: MIT
"""Restart into Windows, Windows Recovery, or the way to Windows Safe Mode.

**Restart into Windows** (unchanged): ``grub-reboot`` picks the existing "Boot into Windows" GRUB entry once, then
reboots. This is the one part of this module that is fully confirmed — it's exactly what the original "Restart into
Windows" shortcut already did.

**Restart into Windows Recovery** does the same, and *also* sets the standard UEFI ``OsIndications`` firmware
variable's ``EFI_OS_INDICATIONS_START_OS_RECOVERY`` bit (``0x20``) before rebooting — the UEFI-spec-defined signal an
OS's boot manager is meant to check for "start in recovery". **This is not independently confirmed to work on
Windows.** The bit itself is real, documented in the UEFI specification, and setting it is exactly as safe as the
well-established "reboot to firmware setup" mechanism (the same variable, a different, confirmed bit, used by
systemd-boot and GNOME's own "Restart to boot options") — but whether this particular build of Windows' own boot
manager acts on *this* bit has not been verified here, despite real effort to check. If it doesn't, the tablet
simply boots into Windows normally: the worst case is no effect, not harm. The real, manual way in (Settings →
Recovery → Advanced startup, or holding Shift while choosing Restart from within Windows) is always shown
alongside this, never only as a fallback for when the automatic attempt fails silently.

**Restart into Windows Safe Mode** makes the same Recovery attempt, because Safe Mode lives inside the Recovery
Environment's own menus (Troubleshoot → Advanced options → Startup Settings → Restart → press 4), not as a separate
boot choice of its own. Reaching it directly, skipping those clicks, would need editing Windows' Boot Configuration
Data (BCD) — a binary registry hive. LintabOS does not write to Windows' registry anywhere (see LinWinMod's own
read-only design, docs/LEGAL.md); a wrong BCD edit can leave Windows unable to boot at all, a risk no amount of
testing here could rule out without a real Windows BCD store to verify against. The last few clicks inside Windows'
own menu are the trade this module makes instead of gambling on that.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

GRUB_WINDOWS_ENTRY = "lintab-windows"
OS_INDICATIONS_VAR = "OsIndications-8be4df61-93ca-11d2-aa0d-00e098032b8c"
EFIVARS_DIR = "/sys/firmware/efi/efivars"
START_OS_RECOVERY = 0x20          # EFI_OS_INDICATIONS_START_OS_RECOVERY, per the UEFI specification
DEFAULT_ATTRS = b"\x07\x00\x00\x00"  # NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS: the standard attribute set

HELPER = "/usr/libexec/lintab/reboot-windows"
MODES = ("normal", "recovery")

RECOVERY_FALLBACK = ("If this doesn't land you in Windows Recovery, you can still get there by hand once Windows "
                     "has started: Settings → System → Recovery → Advanced startup → Restart now, or hold Shift "
                     "while choosing Restart.")
SAFE_MODE_STEPS = ("Once in Windows Recovery: Troubleshoot → Advanced options → Startup Settings → Restart, "
                   "then press 4 (or Fn+4) for Safe Mode.")


def read_os_indications(efivars: str = EFIVARS_DIR) -> tuple[Optional[bytes], int]:
    """(attributes, value) currently stored, or (None, 0) when unreadable (not UEFI, or never set before)."""
    try:
        with open(os.path.join(efivars, OS_INDICATIONS_VAR), "rb") as f:
            data = f.read()
    except OSError:
        return None, 0
    if len(data) < 12:
        return None, 0
    return data[:4], int.from_bytes(data[4:12], "little")


def set_os_indications(bit: int, efivars: str = EFIVARS_DIR) -> bool:
    """OR ``bit`` into the firmware's OsIndications variable. Returns whether the write succeeded. Never raises: a
    tablet that isn't UEFI, or whose firmware refuses the write, just means the Recovery attempt quietly has no
    extra effect — the manual instructions cover that either way."""
    attrs, value = read_os_indications(efivars)
    try:
        with open(os.path.join(efivars, OS_INDICATIONS_VAR), "wb") as f:
            f.write((attrs or DEFAULT_ATTRS) + (value | bit).to_bytes(8, "little"))
        return True
    except OSError:
        return False


def helper_main(argv: list[str], run=subprocess.run, geteuid=os.geteuid) -> int:
    if geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    mode = argv[0] if argv else "normal"
    if mode not in MODES:
        print(f"usage: reboot-windows [{'|'.join(MODES)}]", file=sys.stderr)
        return 2
    reboot_proc = run(["grub-reboot", GRUB_WINDOWS_ENTRY])
    if getattr(reboot_proc, "returncode", 0) != 0:
        print("grub-reboot failed; not rebooting.", file=sys.stderr)
        return 3
    if mode == "recovery":
        set_os_indications(START_OS_RECOVERY)   # best effort, see module docstring; never blocks the reboot
    run(["systemctl", "reboot"])
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-restart-windows", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=MODES, nargs="?", default="normal")
    args = ap.parse_args(argv)
    return subprocess.run(["pkexec", HELPER, args.mode]).returncode


if __name__ == "__main__":
    sys.exit(main())
