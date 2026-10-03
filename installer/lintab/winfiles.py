# SPDX-License-Identifier: MIT
"""Show the Windows drive as "Windows" in the sidebar of GNOME Files.

An /etc/fstab entry does the work: ``x-gvfs-show`` makes GNOME Files list it, ``x-systemd.automount`` mounts it the
first time you open it (no password), and the files belong to you. It is **read-only by default**: Windows with Fast
Startup leaves its disk half-written, and writing to such a disk from Linux can corrupt it. ``--read-write`` is only
accepted when the drive is clean.

A BitLocker-encrypted Windows drive can't be mounted this way (it needs the recovery key); use **Unlock BitLocker
Drive** for that, and its files then appear in the sidebar too.
"""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

BEGIN = "# BEGIN LintabOS Windows files (managed by lintab-windows-files; do not edit between these lines)"
END = "# END LintabOS Windows files"
MOUNT_POINT = "/media/windows"


@dataclass
class WindowsPartition:
    path: str
    uuid: str
    size: int


def fstab_block(uuid: str, uid: int, gid: int, read_write: bool = False, mount_point: str = MOUNT_POINT) -> str:
    opts = [
        "rw" if read_write else "ro", f"uid={uid}", f"gid={gid}", "fmask=0133", "dmask=0022", "windows_names",
        "nofail", "noauto", "x-systemd.automount", "x-systemd.idle-timeout=120",
        "x-gvfs-show", "x-gvfs-name=Windows", "x-gvfs-icon=drive-harddisk",
    ]
    return f"{BEGIN}\nUUID={uuid} {mount_point} ntfs3 {','.join(opts)} 0 0\n{END}\n"


def apply_block(fstab: str, block: Optional[str]) -> str:
    """Return ``fstab`` with our managed block replaced by ``block`` (or removed when ``block`` is None)."""
    lines = fstab.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == BEGIN:
            skipping = True
            continue
        if skipping:
            if line.strip() == END:
                skipping = False
            continue
        out.append(line)
    text = "".join(out)
    if block:
        if text and not text.endswith("\n"):
            text += "\n"
        text += block
    return text


def windows_partitions() -> list[WindowsPartition]:
    """Plain (not BitLocker) NTFS Windows partitions on this computer's disks."""
    from . import disks
    found = []
    for disk in disks.list_disks(hide=""):
        if disk.label != "gpt":
            continue
        part = disks.find_windows(disk)
        if part is None or disks.is_bitlocker(part):
            continue
        uuid = disks.run(["blkid", "-s", "UUID", "-o", "value", part.path], check=False).stdout.strip()
        if uuid:
            found.append(WindowsPartition(part.path, uuid, part.size))
    return found


def add_to_fstab(fstab_path: str, uid: int, gid: int, root: str = "/", read_write: bool = False,
                 partitions: Optional[list[WindowsPartition]] = None) -> Optional[WindowsPartition]:
    """Add the Windows drive to ``fstab_path`` (and create its mount point under ``root``). Used by the installer."""
    partitions = windows_partitions() if partitions is None else partitions
    if not partitions:
        return None
    part = max(partitions, key=lambda p: p.size)
    with open(fstab_path) as f:
        text = f.read()
    with open(fstab_path, "w") as f:
        f.write(apply_block(text, fstab_block(part.uuid, uid, gid, read_write)))
    os.makedirs(os.path.join(root, MOUNT_POINT.lstrip("/")), exist_ok=True)
    return part


def _owner(user: Optional[str]) -> tuple[int, int]:
    if user:
        entry = pwd.getpwnam(user)
    elif os.environ.get("PKEXEC_UID", "").isdigit():
        entry = pwd.getpwuid(int(os.environ["PKEXEC_UID"]))
    elif os.environ.get("SUDO_UID", "").isdigit():
        entry = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    else:
        raise SystemExit("say which user the files should belong to: --user NAME")
    return entry.pw_uid, entry.pw_gid


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-windows-files", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["status", "enable", "disable"], nargs="?", default="status")
    ap.add_argument("--user", help="who the files belong to (default: the user who ran sudo/pkexec)")
    ap.add_argument("--read-write", action="store_true", help="allow writing (refused if Windows left the drive unclean)")
    args = ap.parse_args(argv)

    if args.action == "status":
        with open("/etc/fstab") as f:
            enabled = BEGIN in f.read()
        print(f"Windows files in the sidebar: {'enabled' if enabled else 'not enabled'}")
        for p in windows_partitions():
            print(f"  Windows partition {p.path}  (UUID {p.uuid})")
        return 0
    if os.geteuid() != 0:
        print("run this as root (sudo)", file=sys.stderr)
        return 1

    if args.action == "disable":
        subprocess.run(["umount", MOUNT_POINT], capture_output=True)
        with open("/etc/fstab") as f:
            text = f.read()
        with open("/etc/fstab", "w") as f:
            f.write(apply_block(text, None))
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
        print("removed Windows files from the sidebar")
        return 0

    uid, gid = _owner(args.user)
    parts = windows_partitions()
    if not parts:
        print("No Windows partition found (or it is BitLocker-encrypted: use Unlock BitLocker Drive).", file=sys.stderr)
        return 2
    if args.read_write:
        from . import bitlocker
        info = bitlocker.probe(max(parts, key=lambda p: p.size).path)
        if not info.healthy:
            print("Refusing read-write: Windows left this drive unclean or hibernated (Fast Startup). "
                  "Boot Windows, run `powercfg /h off`, shut down fully, then try again.", file=sys.stderr)
            return 3
    part = add_to_fstab("/etc/fstab", uid, gid, read_write=args.read_write, partitions=parts)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    print(f"Windows ({part.path}) will show as “Windows” in Files, {'read-write' if args.read_write else 'read-only'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
