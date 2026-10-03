# SPDX-License-Identifier: MIT
"""Disk discovery for the LintabOS installer.

Everything here is read-only: it inspects block devices and never writes to
them. All sizes in the model are bytes; sector counts are only used where the
partition table itself speaks sectors (and are converted immediately).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

MiB = 1024 * 1024
GiB = 1024 * MiB

# sfdisk, blkid, mount and friends live in /sbin or /usr/sbin. sudo/pkexec's "secure_path" puts those on PATH for
# anything run as root, but a plain unprivileged desktop session often does not — and this module is explicitly meant
# to be safe to call unprivileged (see the module docstring). Without this, a tool that is genuinely installed can
# still fail with "no such file or directory" purely because of where the caller's PATH happened to look.
_SBIN_DIRS = ("/usr/sbin", "/sbin", "/usr/local/sbin")

# GPT partition type GUIDs (lower-case, as sfdisk prints them).
GUID_ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
GUID_MS_BASIC_DATA = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
GUID_MS_RESERVED = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
GUID_WIN_RE = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
GUID_LINUX_ROOT_X86_64 = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
GUID_LINUX_FS = "0fc63daf-8483-4772-8e79-3d69d8477de4"


class DiskError(RuntimeError):
    pass


def resolve_executable(name: str, which=shutil.which, environ=os.environ) -> str:
    """The full path to ``name``, trying the inherited PATH first and then the usual sbin directories. Falls back to
    the bare name, unchanged, if it can't be found anywhere — so the resulting error is the familiar, clear "no such
    file or directory" from actually trying to run it, rather than this function silently hiding the problem."""
    if "/" in name:        # already a path (or deliberately not a plain command name): leave it alone
        return name
    found = which(name)
    if found:
        return found
    extended = os.pathsep.join([environ.get("PATH", ""), *_SBIN_DIRS])
    return which(name, path=extended) or name


def run(argv: list[str], check: bool = True, input: Optional[str] = None) -> subprocess.CompletedProcess:
    if argv:
        argv = [resolve_executable(argv[0]), *argv[1:]]
    proc = subprocess.run(argv, capture_output=True, text=True, input=input)
    if check and proc.returncode != 0:
        raise DiskError(f"{' '.join(argv)} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
    return proc


@dataclass
class Partition:
    number: int
    path: str
    start: int  # bytes
    size: int  # bytes
    type_guid: str
    uuid: str  # GPT partition GUID
    name: str = ""
    fstype: str = ""  # from lsblk/blkid: ntfs, vfat, ext4, BitLocker, ...
    label: str = ""
    mountpoints: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        """First byte *after* the partition."""
        return self.start + self.size

    @property
    def is_esp(self) -> bool:
        return self.type_guid == GUID_ESP

    @property
    def is_ms_data(self) -> bool:
        return self.type_guid == GUID_MS_BASIC_DATA


@dataclass
class FreeRegion:
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass
class Disk:
    path: str
    size: int
    sector_size: int
    model: str
    transport: str
    removable: bool
    label: str  # "gpt", "dos" or "" for blank
    first_usable: int = MiB
    last_usable: int = 0  # first byte after the last usable byte
    partitions: list[Partition] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        gib = self.size / GiB
        return f"{self.model or self.path} ({gib:.0f} GB, {self.path})"

    def free_regions(self, min_size: int = 64 * MiB) -> list[FreeRegion]:
        """Gaps between partitions, trimmed to 1 MiB boundaries."""
        regions: list[FreeRegion] = []
        cursor = align_up(self.first_usable, MiB)
        limit = align_down(self.last_usable, MiB)
        for part in sorted(self.partitions, key=lambda p: p.start):
            gap_end = align_down(part.start, MiB)
            if gap_end - cursor >= min_size:
                regions.append(FreeRegion(cursor, gap_end - cursor))
            cursor = max(cursor, align_up(part.end, MiB))
        if limit - cursor >= min_size:
            regions.append(FreeRegion(cursor, limit - cursor))
        return regions

    def find_esp(self) -> Optional[Partition]:
        for part in self.partitions:
            if part.is_esp and part.fstype == "vfat":
                return part
        return None


def align_up(value: int, unit: int) -> int:
    return -(-value // unit) * unit


def align_down(value: int, unit: int) -> int:
    return value // unit * unit


def _lsblk(path: Optional[str] = None) -> list[dict]:
    argv = ["lsblk", "-J", "-b", "-o",
            "NAME,PATH,SIZE,TYPE,RM,MODEL,TRAN,FSTYPE,LABEL,MOUNTPOINTS,LOG-SEC"]
    if path:
        argv.append(path)
    return json.loads(run(argv).stdout)["blockdevices"]


def _mountpoints(node: dict) -> list[str]:
    return [m for m in (node.get("mountpoints") or []) if m]


def _flatten(node: dict) -> list[dict]:
    out = [node]
    for child in node.get("children") or []:
        out.extend(_flatten(child))
    return out


def _probe_fstype(path: str) -> str:
    """lsblk only knows what udev has probed; ask libblkid directly as a fallback."""
    proc = run(["blkid", "-p", "-s", "TYPE", "-o", "value", path], check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_disk(path: str) -> Disk:
    """Describe one whole disk, including its GPT partitions."""
    node = _lsblk(path)[0]
    sector = int(node.get("log-sec") or 512)
    disk = Disk(
        path=node["path"],
        size=int(node["size"]),
        sector_size=sector,
        model=(node.get("model") or "").strip(),
        transport=node.get("tran") or "",
        removable=bool(node.get("rm")),
        label="",
        last_usable=int(node["size"]),
    )

    dump = run(["sfdisk", "-J", path], check=False)
    if dump.returncode != 0 or not dump.stdout.strip():
        return disk  # blank disk, no partition table yet

    table = json.loads(dump.stdout)["partitiontable"]
    disk.label = table.get("label", "")
    if disk.label != "gpt":
        # MBR disks are not supported for dual-boot: the target is a UEFI tablet.
        return disk

    disk.first_usable = int(table.get("firstlba", 34)) * sector
    disk.last_usable = (int(table.get("lastlba", disk.size // sector - 34)) + 1) * sector

    children = {c["path"]: c for c in _flatten(node) if c is not node}
    for entry in table.get("partitions", []):
        ppath = entry["node"]
        info = children.get(ppath, {})
        number = int(re.search(r"(\d+)$", ppath).group(1))
        disk.partitions.append(Partition(
            number=number,
            path=ppath,
            start=int(entry["start"]) * sector,
            size=int(entry["size"]) * sector,
            type_guid=entry["type"].lower(),
            uuid=entry.get("uuid", "").lower(),
            name=entry.get("name", ""),
            fstype=info.get("fstype") or _probe_fstype(ppath),
            label=info.get("label") or "",
            mountpoints=_mountpoints(info),
        ))
    return disk


def live_medium_disk() -> Optional[str]:
    """Whole-disk path the live system booted from, so it can be hidden."""
    for target in ("/run/live/medium", "/cdrom", "/lib/live/mount/medium"):
        proc = run(["findmnt", "-n", "-o", "SOURCE", target], check=False)
        src = proc.stdout.strip()
        if src:
            parent = run(["lsblk", "-n", "-o", "PKNAME", src], check=False).stdout.strip().splitlines()
            if parent and parent[0]:
                return "/dev/" + parent[0]
            return src
    return None


def list_disks(hide: Optional[str] = None) -> list[Disk]:
    hide = hide if hide is not None else live_medium_disk()
    disks = []
    for node in _lsblk():
        if node["type"] != "disk":
            continue
        name = node["path"]
        if name == hide or re.match(r"/dev/(loop|zram|ram|sr|fd)", name):
            continue
        if int(node["size"]) < 16 * GiB:
            continue
        disks.append(read_disk(name))
    return disks


def is_bitlocker(part: Partition) -> bool:
    """BitLocker volumes carry '-FVE-FS-' at byte 3 of the boot sector."""
    if part.fstype.lower() == "bitlocker":
        return True
    try:
        with open(part.path, "rb") as dev:
            dev.seek(3)
            return dev.read(8) == b"-FVE-FS-"
    except OSError:
        return False


def find_windows(disk: Disk) -> Optional[Partition]:
    """The NTFS/BitLocker partition that holds \\Windows, if any.

    BitLocker volumes cannot be inspected, so they are accepted when they are
    the largest Microsoft-data partition on a disk that also has a Windows
    boot manager on its ESP (checked by the caller via :func:`esp_has_windows`).
    """
    candidates = [p for p in disk.partitions
                  if p.is_ms_data and (p.fstype in ("ntfs",) or is_bitlocker(p))]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.size, reverse=True)
    for part in candidates:
        if is_bitlocker(part):
            return part
        if _has_windows_dir(part):
            return part
    return None


def _has_windows_dir(part: Partition) -> bool:
    import tempfile
    mnt = tempfile.mkdtemp(prefix="lintab-ntfs-")
    try:
        proc = run(["mount", "-t", "ntfs3", "-o", "ro,noatime", part.path, mnt], check=False)
        if proc.returncode != 0:
            proc = run(["ntfs-3g", "-o", "ro,noatime", part.path, mnt], check=False)
        if proc.returncode != 0:
            return False
        try:
            return os.path.isdir(os.path.join(mnt, "Windows", "System32"))
        finally:
            run(["umount", mnt], check=False)
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass


def esp_free_bytes(esp: Partition) -> int:
    import tempfile
    mnt = tempfile.mkdtemp(prefix="lintab-esp-")
    try:
        run(["mount", "-o", "ro", esp.path, mnt])
        try:
            st = os.statvfs(mnt)
            return st.f_bavail * st.f_frsize
        finally:
            run(["umount", mnt], check=False)
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass


def esp_has_windows(esp: Partition) -> bool:
    import tempfile
    mnt = tempfile.mkdtemp(prefix="lintab-esp-")
    try:
        if run(["mount", "-o", "ro", esp.path, mnt], check=False).returncode != 0:
            return False
        try:
            return any(os.path.isfile(os.path.join(mnt, "EFI", "Microsoft", "Boot", name))
                       for name in ("bootmgfw.efi", "BOOTMGFW.EFI"))
        finally:
            run(["umount", mnt], check=False)
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass
