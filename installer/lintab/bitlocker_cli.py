# SPDX-License-Identifier: MIT
"""``lintab-bitlocker``: unlock, browse, lock and decrypt BitLocker volumes.

Recovery keys are read from a prompt or stdin, never from the command line.
The GUI apps call this tool through pkexec, so it is also the privileged helper.
"""

from __future__ import annotations

import argparse
import getpass
import os
import pwd
import subprocess
import sys
import time

from . import bitlocker, bitlocker_decrypt as bd, disks
from .disks import GiB


def _key(args: argparse.Namespace) -> str:
    if args.key_stdin:
        return sys.stdin.readline().strip()
    return getpass.getpass("Recovery key (48 digits): ")


def cmd_list(_args) -> int:
    found = []
    for disk in disks.list_disks(hide=""):
        for part in disk.partitions:
            if disks.is_bitlocker(part):
                found.append((part, disk))
    if not found:
        print("No BitLocker volumes found.")
        return 1
    for part, disk in found:
        mounted = os.path.ismount(os.path.join(bitlocker.RUN_ROOT, bitlocker._name_for(part.path), "mnt"))
        print(f"{part.path}\t{part.size / GiB:.1f} GB\t{disk.model}\t{'unlocked' if mounted else 'locked'}")
    return 0


def cmd_unlock(args) -> int:
    try:
        handle = bitlocker.unlock(args.device, _key(args), read_only=not args.read_write)
        mount_point = args.mountpoint or None
        if args.mount:
            uid = args.uid if args.uid is not None else _pkexec_uid()
            gid = pwd.getpwuid(uid).pw_gid if uid is not None else None
            if mount_point is None and uid is not None:
                user = pwd.getpwuid(uid).pw_name
                mount_point = os.path.join("/run/media", user, "BitLocker-" + handle.name)
            mount_point = bitlocker.mount_view(handle, mount_point, read_only=not args.read_write, uid=uid, gid=gid)
            print(mount_point)
        else:
            print(handle.view)
    except bitlocker.BitLockerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _pkexec_uid():
    value = os.environ.get("PKEXEC_UID")
    return int(value) if value and value.isdigit() else None


def cmd_lock(args) -> int:
    for entry in (args.device,):
        name = bitlocker._name_for(entry)
        for candidate in _media_mounts(name):
            subprocess.run(["umount", candidate], capture_output=True)
            try:
                os.rmdir(candidate)
            except OSError:
                pass
    bitlocker.lock(args.device)
    return 0


def _media_mounts(name: str) -> list[str]:
    found = []
    try:
        for line in open("/proc/mounts"):
            target = line.split()[1]
            if target.startswith("/run/media/") and target.endswith("BitLocker-" + name):
                found.append(target)
    except OSError:
        pass
    return found


def _print_progress(phase: str, done: int, total: int, message: str) -> None:
    pct = 100 * done / total if total else 100
    sys.stdout.write(f"\r{message:<40} {pct:5.1f}%")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


def cmd_decrypt(args) -> int:
    key = _key(args)
    try:
        pre = bd.preflight(args.device, key, args.journal_dir)
    except (bd.DecryptError, bitlocker.BitLockerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Drive: {pre.info.size / GiB:.1f} GB, {pre.info.used / GiB:.1f} GB used; "
          f"Windows boot files found: {pre.boot_files}; charger: {'yes' if pre.ac_power else 'NO'}")
    for line in pre.warnings:
        print("warning:", line)
    if not pre.ok:
        for line in pre.problems:
            print("problem:", line, file=sys.stderr)
        return 2
    if args.check_only:
        print("All checks passed; nothing was changed.")
        return 0
    if args.yes != "DECRYPT":
        print("Refusing to write: pass --yes DECRYPT to confirm. This rewrites the whole partition.", file=sys.stderr)
        return 3
    started = time.time()
    try:
        bd.decrypt_in_place(args.device, key, args.journal_dir, progress=_print_progress)
    except bd.DecryptInterrupted as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 4
    except (bd.DecryptError, bitlocker.BitLockerError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2
    print(f"BitLocker is off ({(time.time() - started) / 60:.1f} min).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lintab-bitlocker", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show BitLocker volumes").set_defaults(fn=cmd_list)

    u = sub.add_parser("unlock", help="unlock with the recovery key (read-only unless --read-write)")
    u.add_argument("device")
    u.add_argument("--mount", action="store_true", help="also mount the Windows files")
    u.add_argument("--mountpoint")
    u.add_argument("--read-write", action="store_true")
    u.add_argument("--uid", type=int)
    u.add_argument("--key-stdin", action="store_true", help="read the key from stdin instead of prompting")
    u.set_defaults(fn=cmd_unlock)

    l = sub.add_parser("lock", help="unmount and close")
    l.add_argument("device")
    l.set_defaults(fn=cmd_lock)

    d = sub.add_parser("decrypt", help="turn BitLocker off by decrypting the partition in place")
    d.add_argument("device")
    d.add_argument("--journal-dir", required=True, help="directory on the EFI partition for the recovery journal")
    d.add_argument("--check-only", action="store_true", help="run every check, change nothing")
    d.add_argument("--yes", default="", help="pass DECRYPT to confirm")
    d.add_argument("--key-stdin", action="store_true")
    d.set_defaults(fn=cmd_decrypt)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
