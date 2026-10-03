# SPDX-License-Identifier: MIT
"""LinWinMod: edit the Windows filesystem, and browse the Windows registry, from LintabOS.

Two separate capabilities, with different rules:

* **Files**: a normal file manager on the Windows partition, read-write (the same safety checks as `lintab-windows-files
  --read-write`: refused if BitLocker is still locked, or if Windows left the drive dirty or hibernated). This is no more
  capable than GNOME Files already is on a drive mounted read-write; it adds no new way to change Windows.

* **Registry**: browsing only, **never writing**. ``SAM`` and ``SECURITY`` are refused outright, at the function that opens a
  hive, not just in the menu — ``SAM`` holds the account password hashes and ``SECURITY`` holds cached credentials and LSA
  secrets, so opening either hands over credential material directly. The other hives (``SOFTWARE``, ``SYSTEM``, ``DEFAULT``,
  and each user's ``NTUSER.DAT``/``UsrClass.dat``) are open for reading: that is where you look to work out why Windows
  won't boot, what a service points at, or what a setting is, without handing over a way to change Windows' security state.
  There is deliberately no write path here at all: the registry is how Windows' own security policy, autologon and service
  configuration are controlled, and no subset of it can be safely carved out as "editable" (see docs/LEGAL.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

MOUNT_POINT = "/media/windows"

# Hive file names (case-insensitive on NTFS) and whether LinWinMod will open them at all.
BLOCKED_HIVES = frozenset({"sam", "security"})
MACHINE_HIVES = ("SOFTWARE", "SYSTEM", "DEFAULT")  # SAM and SECURITY are deliberately not listed here

REG_TYPE_NAMES = {
    0: "REG_NONE", 1: "REG_SZ", 2: "REG_EXPAND_SZ", 3: "REG_BINARY", 4: "REG_DWORD",
    5: "REG_DWORD_BIG_ENDIAN", 6: "REG_LINK", 7: "REG_MULTI_SZ", 8: "REG_RESOURCE_LIST",
    9: "REG_FULL_RESOURCE_DESCRIPTOR", 10: "REG_RESOURCE_REQUIREMENTS_LIST", 11: "REG_QWORD",
}


class BlockedHiveError(RuntimeError):
    """Raised for SAM/SECURITY: refuse to open, not just hide from a menu."""


@dataclass(frozen=True)
class HiveRef:
    label: str     # shown in the picker, e.g. "SOFTWARE" or "ada (user)"
    path: str      # real path on the mounted Windows partition


def is_blocked_hive(path: str) -> bool:
    return os.path.basename(path).lower() in BLOCKED_HIVES


def find_hives(windows_root: str, listdir=os.listdir, isfile=os.path.isfile) -> list[HiveRef]:
    """The hives LinWinMod is willing to open on a mounted Windows partition. SAM/SECURITY are never included,
    even though they are right there on disk next to the others."""
    found: list[HiveRef] = []
    config = os.path.join(windows_root, "Windows", "System32", "config")
    try:
        names = {n.lower(): n for n in listdir(config)}
    except OSError:
        names = {}
    for wanted in MACHINE_HIVES:
        real = names.get(wanted.lower())
        if real and isfile(os.path.join(config, real)):
            found.append(HiveRef(wanted, os.path.join(config, real)))

    users_dir = os.path.join(windows_root, "Users")
    try:
        user_names = listdir(users_dir)
    except OSError:
        user_names = []
    for user in sorted(user_names):
        if user.lower() in ("default", "public", "all users", "default user"):
            continue
        ntuser = os.path.join(users_dir, user, "NTUSER.DAT")
        if isfile(ntuser):
            found.append(HiveRef(f"{user} (user)", ntuser))
        usrclass = os.path.join(users_dir, user, "AppData", "Local", "Microsoft", "Windows", "UsrClass.dat")
        if isfile(usrclass):
            found.append(HiveRef(f"{user} (user classes)", usrclass))
    return found


def open_hive(path: str):
    """Open a hive **read-only**. Refuses SAM/SECURITY regardless of how the path is spelled or reached."""
    if is_blocked_hive(path):
        raise BlockedHiveError(
            f"{os.path.basename(path)} is not opened by LinWinMod: it holds account password hashes or cached "
            "credentials, not configuration. Reading it would hand over credential material, not just let you look "
            "around, so this is refused regardless of the menu.")
    import hivex
    return hivex.Hivex(path, write=False)


@dataclass
class RegValue:
    name: str          # "" is the default (unnamed) value
    type_code: int
    type_name: str
    display: str        # human-readable rendering; never raises on odd/corrupt data


def _hex_preview(data: bytes, limit: int = 64) -> str:
    shown = data[:limit]
    text = " ".join(f"{b:02x}" for b in shown)
    return text + (" …" if len(data) > limit else "")


def describe_value(h, value) -> RegValue:
    """A value handle -> a RegValue that is always safe to print, whatever type or garbage is actually stored."""
    name = h.value_key(value)
    type_code = h.value_type(value)[0]
    type_name = REG_TYPE_NAMES.get(type_code, f"type {type_code}")
    try:
        if type_code in (1, 2):          # REG_SZ, REG_EXPAND_SZ
            display = h.value_string(value)
        elif type_code == 4:             # REG_DWORD
            display = f"0x{h.value_dword(value):08x} ({h.value_dword(value)})"
        elif type_code == 11:            # REG_QWORD
            display = f"0x{h.value_qword(value):016x} ({h.value_qword(value)})"
        elif type_code == 7:             # REG_MULTI_SZ
            strings = h.value_multiple_strings(value)
            if strings and strings[-1] == "":   # the list's own double-null terminator, not an extra empty string
                strings = strings[:-1]
            display = "\n".join(strings)
        else:
            display = _hex_preview(h.value_value(value)[1])
    except Exception as exc:  # noqa: BLE001 - a malformed value must still be listed, not crash the browser
        display = f"(could not read this value: {exc})"
    return RegValue(name, type_code, type_name, display)


@dataclass
class RegNode:
    name: str
    child_names: list[str] = field(default_factory=list)
    values: list[RegValue] = field(default_factory=list)


def read_node(h, node) -> RegNode:
    children = sorted((h.node_name(c) for c in h.node_children(node)), key=str.lower)
    values = [describe_value(h, v) for v in h.node_values(node)]
    values.sort(key=lambda rv: rv.name.lower())
    return RegNode(h.node_name(node), children, values)


def navigate(h, path_parts: list[str]):
    """Walk from the root through ``path_parts`` (as produced by splitting a displayed path). Raises KeyError with the
    part that was not found, so the caller can show exactly where the path stopped existing."""
    node = h.root()
    for part in path_parts:
        nxt = h.node_get_child(node, part)
        if nxt is None:
            raise KeyError(part)
        node = nxt
    return node


def search_keys(h, needle: str, limit: int = 200) -> list[str]:
    """Key paths (backslash-separated) whose last component contains ``needle`` (case-insensitive). Depth-first,
    stops at ``limit`` matches so a huge hive can't hang the UI."""
    needle = needle.lower()
    matches: list[str] = []

    def walk(node, path: list[str], is_root: bool) -> None:
        if len(matches) >= limit:
            return
        name = h.node_name(node)
        here = path if is_root else path + [name]  # the root's own synthetic name is never part of a displayed path
        if not is_root and needle in name.lower():
            matches.append("\\".join(here))
        for child in h.node_children(node):
            if len(matches) >= limit:
                return
            walk(child, here, is_root=False)

    walk(h.root(), [], is_root=True)
    return matches


# --------------------------------------------------------------------- files --

@dataclass
class MountStatus:
    partition: Optional[object]   # a winfiles.WindowsPartition, or None if no Windows partition was found
    mount_point: str
    mounted: bool
    read_write: bool


def mount_status(mount_point: str = MOUNT_POINT, proc_mounts: str = "/proc/mounts") -> tuple[bool, bool]:
    """(mounted, read_write) by reading the real mount table, not assuming fstab matches reality."""
    try:
        with open(proc_mounts) as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 4 and fields[1] == mount_point:
                    options = fields[3].split(",")
                    return True, "rw" in options
    except OSError:
        pass
    return False, False


def ensure_writable(run=subprocess.run) -> subprocess.CompletedProcess:
    """Remount read-write (or mount read-write if not mounted). The health check (BitLocker/dirty/hibernated) is the
    caller's job — see lintab.bitlocker.probe — this only does the mount itself, as root, via the helper + polkit."""
    return run(["pkexec", REMOUNT_HELPER, "rw"], capture_output=True, text=True)


def ensure_read_only(run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["pkexec", REMOUNT_HELPER, "ro"], capture_output=True, text=True)


def open_file_manager(path: str, run=subprocess.Popen) -> None:
    run(["xdg-open", path])


# ---------------------------------------------------------- privileged remount helper --
# Deliberately tiny: it only ever remounts the one mount point that lintab-windows-files already declared in /etc/fstab
# (with its UUID validated there), between the ro and rw options already set up for it. It cannot be pointed at an
# arbitrary device or path.

REMOUNT_HELPER = "/usr/libexec/lintab/winmod-remount"


def remount_helper_main(argv: list[str], fstab_path: str = "/etc/fstab", mount_point: str = MOUNT_POINT,
                        run=subprocess.run, geteuid=os.geteuid) -> int:
    if geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    if argv not in (["rw"], ["ro"]):
        print("usage: winmod-remount rw|ro", file=sys.stderr)
        return 2
    mode = argv[0]
    from . import winfiles
    try:
        with open(fstab_path) as f:
            declared = winfiles.BEGIN in f.read()
    except OSError:
        declared = False
    if not declared:
        print(f"{mount_point} is not set up yet. Open “Windows Files” (lintab-windows-files enable) first.", file=sys.stderr)
        return 3
    mounted, _rw = mount_status(mount_point)
    if not mounted:
        proc = run(["mount", mount_point], capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"could not mount {mount_point}: {(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
            return 4
    proc = run(["mount", "-o", f"remount,{mode}", mount_point], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"could not remount {mount_point} {mode}: {(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
        return 5
    print(f"{mount_point} is now {mode}")
    return 0
