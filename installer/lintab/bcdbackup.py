# SPDX-License-Identifier: MIT
"""Windows Boot Configuration Data (BCD): a backed-up, verified write of the ``onetimeadvancedoptions`` flag -
the flag ``shutdown /r /o`` / Shift+Restart sets, which is what actually opens Windows' Troubleshoot/Advanced
options menu (unlike the UEFI ``OsIndications`` bit in :mod:`lintab.winrecovery`, which real-hardware testing
showed only triggers an Automatic Repair check, not that menu).

**Status: experimental, not yet confirmed on a real installed Windows system.** The write path itself (hivex
``node_add_child``/``node_set_value``/``commit``) was verified, round-tripped through a second, independent tool
(``hivexsh``), against real Microsoft-authored BCD samples extracted from a Windows installation ISO - see
``tests/fixtures/real-bcd-samples/``. What has *not* yet been confirmed is that writing this on an *installed*
system's live BCD, then rebooting, actually opens the Troubleshoot menu on real hardware, or that this handles
whatever structural differences a real installed-system BCD may have from the setup-media samples it was
validated against (more boot entries, prior edits, a different Windows version).

Because of that gap, this never runs unattended and never replaces the existing, confirmed-safe "Restart into
Windows Recovery" feature - it is a separate, clearly-labeled experimental tool, gated behind real safety checks
every time it runs:

* refuses unless connected to AC power and at least ``MIN_BATTERY_PERCENT`` charged. The actual write is too
  fast (milliseconds) to meaningfully "catch" a power-unplug event mid-write and abort it, so this is a
  pre-flight gate, not a live interrupt - the ESP is FAT32, which is not journaled, and a write torn by power
  loss can corrupt more than just the one file;
* always backs up the live BCD before writing, to a path LintabOS can read back regardless of whether Windows
  still boots;
* immediately re-opens and verifies the just-written BCD in a *fresh* hivex handle - not just trusting that
  ``commit()`` returning without an exception means the result is correct;
* automatically restores the backup the moment that verification fails, for any reason (a torn write, a
  structural surprise, anything), before ever returning control to the caller.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional

# -------------------------------------------------------------------------------------------------- power gate --
# A torn write to a FAT32 ESP (not journaled) from a mid-write power loss can corrupt more than the one file, so
# this refuses to even attempt the write unless power looks safe. "Can't tell" is treated as "not safe", not as
# "assume fine" - see power_safe_to_write.

POWER_SUPPLY_DIR = "/sys/class/power_supply"
MIN_BATTERY_PERCENT = 40
_AC_SUPPLY_TYPES = ("Mains", "USB", "USB_PD", "USB_PD_DRP", "USB_DCP", "USB_CDP", "USB_ACA")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    text = _read_text(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def ac_connected(power_supply_dir: str = POWER_SUPPLY_DIR, listdir=os.listdir) -> Optional[bool]:
    """True/False once a Mains or USB power supply is found and its ``online`` file read. None if no such supply
    exists at all (can't tell either way) - callers must treat that the same as "not connected", never as "fine"."""
    try:
        names = listdir(power_supply_dir)
    except OSError:
        return None
    found = False
    for name in names:
        if _read_text(os.path.join(power_supply_dir, name, "type")) in _AC_SUPPLY_TYPES:
            found = True
            if _read_int(os.path.join(power_supply_dir, name, "online")) == 1:
                return True
    return False if found else None


def battery_percent(power_supply_dir: str = POWER_SUPPLY_DIR, listdir=os.listdir) -> Optional[int]:
    try:
        names = listdir(power_supply_dir)
    except OSError:
        return None
    for name in names:
        if _read_text(os.path.join(power_supply_dir, name, "type")) == "Battery":
            return _read_int(os.path.join(power_supply_dir, name, "capacity"))
    return None


def power_safe_to_write(power_supply_dir: str = POWER_SUPPLY_DIR, min_battery: int = MIN_BATTERY_PERCENT,
                        listdir=os.listdir) -> tuple[bool, str]:
    """(ok, reason). Refuses - with a reason a user can act on - unless AC is connected and the battery (if any
    is reported at all) is at or above ``min_battery``."""
    ac = ac_connected(power_supply_dir, listdir)
    if ac is None:
        return False, "Could not tell whether this device is on AC power - refusing to risk an interrupted write."
    if not ac:
        return False, "Not connected to a charger. Plug in before writing the boot configuration."
    battery = battery_percent(power_supply_dir, listdir)
    if battery is not None and battery < min_battery:
        return False, f"Battery is at {battery}%, below the required {min_battery}%. Charge it further first."
    return True, ""


# --------------------------------------------------------------------------------------------- BCD hive logic --
# Pure functions over a local file path - no mounting involved, so these are directly testable against the real
# BCD samples in tests/fixtures/real-bcd-samples/.

BOOTMGR_GUID = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}"   # {bootmgr}: constant across every real Windows install
DEFAULT_OBJECT_ELEMENT = "23000003"                       # BcdBootMgrObject_DefaultObject
ONETIME_ADVANCED_OPTIONS_ELEMENT = "260000c3"              # what shutdown /r /o actually sets


class BcdStructureError(RuntimeError):
    """The BCD doesn't look like what this code expects. Raised instead of guessing, so a write never lands on
    the wrong object."""


def _find_value(h, node, key: str):
    for v in h.node_values(node):
        if h.value_key(v) == key:
            return v
    return None


def _default_object_guid(h) -> Optional[str]:
    """The GUID of the OS entry {bootmgr} currently boots by default, read from its own
    BcdBootMgrObject_DefaultObject element. None if the structure isn't what's expected anywhere along the way."""
    root = h.root()
    objects = h.node_get_child(root, "Objects")
    bootmgr = h.node_get_child(objects, BOOTMGR_GUID) if objects is not None else None
    elements = h.node_get_child(bootmgr, "Elements") if bootmgr is not None else None
    element = h.node_get_child(elements, DEFAULT_OBJECT_ELEMENT) if elements is not None else None
    if element is None:
        return None
    value = _find_value(h, element, "Element")
    if value is None:
        return None
    type_code = h.value_type(value)[0]
    if type_code in (1, 2):   # REG_SZ, REG_EXPAND_SZ - the only encodings a GUID reference is expected to use
        return h.value_string(value)
    return None


def _target_elements_node(h, guid: str):
    root = h.root()
    objects = h.node_get_child(root, "Objects")
    target = h.node_get_child(objects, guid) if objects is not None else None
    if target is None:
        raise BcdStructureError(f"the default boot entry {guid} does not exist under \\Objects")
    elements = h.node_get_child(target, "Elements")
    if elements is None:
        raise BcdStructureError(f"{guid} has no \\Elements subkey")
    return elements


def set_onetime_advanced_options(hive_path: str, enabled: bool = True) -> None:
    """Add or update the onetimeadvancedoptions element on the *default* OS entry (resolved dynamically from
    {bootmgr}, not hard-coded - every real install has its own default-entry GUID). Raises BcdStructureError on
    any structural surprise rather than guessing which object to touch."""
    import hivex
    h = hivex.Hivex(hive_path, write=True)
    guid = _default_object_guid(h)
    if guid is None:
        raise BcdStructureError("could not resolve {bootmgr}'s default boot entry (BcdBootMgrObject_DefaultObject)")
    elements = _target_elements_node(h, guid)
    value = bytes([1 if enabled else 0])
    existing = h.node_get_child(elements, ONETIME_ADVANCED_OPTIONS_ELEMENT)
    node = existing if existing is not None else h.node_add_child(elements, ONETIME_ADVANCED_OPTIONS_ELEMENT)
    h.node_set_value(node, {"key": "Element", "t": 3, "value": value})
    h.commit(None)


def verify_onetime_advanced_options(hive_path: str, expected: bool = True) -> bool:
    """Re-open hive_path fresh (a new hivex handle, never the one that just wrote it) and confirm the element
    round-tripped with exactly the expected value. False for anything short of an exact match: a missing
    element, a wrong type, a wrong value, or a hive that no longer parses at all are all just "not verified" -
    the caller's job is to restore the backup the moment this returns False, not to work out which case it was."""
    import hivex
    try:
        h = hivex.Hivex(hive_path, write=False)
        guid = _default_object_guid(h)
        if guid is None:
            return False
        elements = _target_elements_node(h, guid)
        element = h.node_get_child(elements, ONETIME_ADVANCED_OPTIONS_ELEMENT)
        if element is None:
            return False
        value = _find_value(h, element, "Element")
        if value is None:
            return False
        type_code, _length = h.value_type(value)
        data = h.value_value(value)[1]
        return type_code == 3 and data == bytes([1 if expected else 0])
    except Exception:  # noqa: BLE001 - a hive that fails to even open is simply "not verified", never a crash
        return False


# ------------------------------------------------------------------------------------- finding and mounting the ESP --

BCD_SUBPATH = os.path.join("EFI", "Microsoft", "Boot", "BCD")
BACKUP_DIR = "/var/lib/lintab/bcd-backup"
BACKUP_PATH = os.path.join(BACKUP_DIR, "BCD.bak")


def find_windows_esp(run=subprocess.run):
    """The EFI System Partition that holds a Windows boot manager, on whichever disk has one. None if there
    isn't one."""
    from . import disks
    for disk in disks.list_disks():
        esp = disk.find_esp()
        if esp is not None and disks.esp_has_windows(esp):
            return esp
    return None


class MountedEsp:
    """Context manager: mounts an ESP read-write for the duration of the ``with`` block, always unmounts after -
    even if the block raises."""

    def __init__(self, esp_path: str, run=subprocess.run):
        self.esp_path = esp_path
        self.run = run
        self.mount_point: Optional[str] = None

    def __enter__(self) -> str:
        self.mount_point = tempfile.mkdtemp(prefix="lintab-bcd-")
        proc = self.run(["mount", self.esp_path, self.mount_point], capture_output=True, text=True)
        if proc.returncode != 0:
            os.rmdir(self.mount_point)
            raise OSError(f"could not mount {self.esp_path}: {(proc.stderr or proc.stdout).strip()}")
        return self.mount_point

    def __exit__(self, *exc) -> None:
        self.run(["umount", self.mount_point], capture_output=True, text=True)
        try:
            os.rmdir(self.mount_point)
        except OSError:
            pass


def backup_bcd(mount_point: str, backup_path: str = BACKUP_PATH) -> None:
    """Copy the live BCD to backup_path (creating its directory if needed). Raises OSError on any failure - the
    caller must never proceed to write if the backup itself did not succeed."""
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    shutil.copy2(os.path.join(mount_point, BCD_SUBPATH), backup_path)


def restore_bcd(mount_point: str, backup_path: str = BACKUP_PATH) -> None:
    shutil.copy2(backup_path, os.path.join(mount_point, BCD_SUBPATH))


def has_backup(backup_path: str = BACKUP_PATH) -> bool:
    return os.path.isfile(backup_path)


# -------------------------------------------------------------------------------------------------- orchestration --

@dataclass
class WriteResult:
    ok: bool
    message: str
    restored: bool = False


def write_onetime_advanced_options_safely(run=subprocess.run, power_supply_dir: str = POWER_SUPPLY_DIR,
                                          min_battery: int = MIN_BATTERY_PERCENT,
                                          backup_path: str = BACKUP_PATH) -> WriteResult:
    """The whole safe-write flow: power gate, find the ESP, mount it read-write, back up the live BCD, write the
    element, verify the result in a fresh hivex handle, and automatically restore the backup the moment that
    verification fails for any reason. Must run as root (mounting requires it) - see helper_main."""
    ok, reason = power_safe_to_write(power_supply_dir, min_battery)
    if not ok:
        return WriteResult(False, reason)

    esp = find_windows_esp(run)
    if esp is None:
        return WriteResult(False, "No Windows boot manager found on any disk's EFI System Partition.")

    try:
        with MountedEsp(esp.path, run) as mount_point:
            bcd_path = os.path.join(mount_point, BCD_SUBPATH)
            if not os.path.isfile(bcd_path):
                return WriteResult(False, f"No BCD file at {BCD_SUBPATH} on the Windows ESP.")
            try:
                backup_bcd(mount_point, backup_path)
            except OSError as exc:
                return WriteResult(False, "Could not back up the live BCD before writing - refusing to write "
                                          f"without a backup in place: {exc}")
            try:
                set_onetime_advanced_options(bcd_path, enabled=True)
            except Exception as exc:  # noqa: BLE001 - any write failure: restore and report, never leave it half-done
                restore_bcd(mount_point, backup_path)
                return WriteResult(False, f"The write failed ({exc}); the original BCD has been restored.",
                                   restored=True)
            if not verify_onetime_advanced_options(bcd_path, expected=True):
                restore_bcd(mount_point, backup_path)
                return WriteResult(False, "The write did not verify correctly after a fresh re-read; the "
                                          "original BCD has been restored.", restored=True)
            return WriteResult(True, "Written and verified. The next restart will open Windows' "
                                     "Troubleshoot/Advanced options menu.")
    except OSError as exc:
        return WriteResult(False, str(exc))


def restore_backup_now(run=subprocess.run, backup_path: str = BACKUP_PATH) -> WriteResult:
    """Manually restore the backed-up BCD, independent of any write attempt - the tool to reach for if something
    looks wrong after a write, or just to undo it."""
    if not has_backup(backup_path):
        return WriteResult(False, "No backup exists to restore from.")
    esp = find_windows_esp(run)
    if esp is None:
        return WriteResult(False, "No Windows boot manager found on any disk's EFI System Partition.")
    try:
        with MountedEsp(esp.path, run) as mount_point:
            restore_bcd(mount_point, backup_path)
            return WriteResult(True, "The backed-up BCD has been restored.")
    except OSError as exc:
        return WriteResult(False, str(exc))


# --------------------------------------------------------------------------------------- privileged helper + CLI --

HELPER = "/usr/libexec/lintab/bcd-write"
MODES = ("write", "restore")


def helper_main(argv: list[str], run=subprocess.run, geteuid=os.geteuid) -> int:
    if geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    if argv not in (["write"], ["restore"]):
        print(f"usage: bcd-write {'|'.join(MODES)}", file=sys.stderr)
        return 2
    result = write_onetime_advanced_options_safely(run) if argv[0] == "write" else restore_backup_now(run)
    print(result.message)
    return 0 if result.ok else 3


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mode = argv[0] if argv else "write"
    if mode not in MODES:
        print(f"usage: lintab-bcd-write [{'|'.join(MODES)}]", file=sys.stderr)
        return 2
    return subprocess.run(["pkexec", HELPER, mode]).returncode


if __name__ == "__main__":
    sys.exit(main())
