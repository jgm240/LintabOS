# SPDX-License-Identifier: MIT
"""Partition planning and execution for LintabOS.

A *plan* is an ordered list of steps, each one a single external command with a
human-readable description. Planning never touches the disk; :func:`apply_plan`
runs the steps (or just prints them in dry-run mode). This split is what makes
the dual-boot flow reviewable: the GUI shows the exact plan before anything is
written, and the tests assert on it.

Dual-boot strategy (the part that must not lose the user's Windows):
  1. refuse BitLocker / hibernated / dirty NTFS volumes with a clear reason
  2. back up the partition table
  3. shrink the NTFS *filesystem* first (``ntfsresize``), test run before real run
  4. shrink the partition entry to match (``sfdisk -N``, keeps type/GUID/name)
  5. create the Linux partition in the freed space
  6. reuse the existing EFI System Partition; never reformat it
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from .disks import (
    GUID_ESP, GUID_LINUX_ROOT_X86_64, GiB, MiB, Disk, FreeRegion, Partition,
    align_down, align_up, esp_free_bytes, esp_has_windows, find_windows,
    is_bitlocker, run,
)

MIN_LINUX_SIZE = 12 * GiB
MIN_WINDOWS_HEADROOM = 4 * GiB  # keep at least this much free inside Windows after shrinking
ESP_SIZE = 512 * MiB
MIN_ESP_FREE = 16 * MiB


class PlanError(RuntimeError):
    """The requested layout cannot be made safely. Message is user-facing."""


@dataclass
class Step:
    description: str
    argv: list[str]
    input: Optional[str] = None
    destructive: bool = True
    # Non-zero exit is tolerated for steps that are only best-effort (e.g. partprobe).
    allow_failure: bool = False


@dataclass
class Plan:
    disk: str
    mode: str  # "wipe" | "dualboot" | "free"
    steps: list[Step] = field(default_factory=list)
    root_partuuid: str = ""
    esp_partuuid: str = ""  # partuuid of the ESP that will hold GRUB
    esp_is_new: bool = False
    windows_present: bool = False
    summary: list[str] = field(default_factory=list)
    backup_path: str = ""

    def describe(self) -> str:
        lines = [f"Plan for {self.disk} ({self.mode}):"]
        lines += [f"  * {s}" for s in self.summary]
        lines.append("Steps:")
        for i, step in enumerate(self.steps, 1):
            lines.append(f"  {i:2d}. {step.description}")
            lines.append(f"      $ {' '.join(step.argv)}")
            if step.input:
                lines.append(f"        <<< {step.input.strip()}")
        return "\n".join(lines)


# --------------------------------------------------------------------- NTFS --

@dataclass
class NtfsInfo:
    size: int
    min_size: int  # smallest size the filesystem can be resized to


_RESIZE_RE = re.compile(r"You might resize at (\d+) bytes")
_SIZE_RE = re.compile(r"Current volume size:\s*(\d+) bytes")


def probe_ntfs(part: Partition) -> NtfsInfo:
    """Ask ntfsresize how far the volume can shrink. Raises PlanError on any
    condition that makes shrinking unsafe, with advice the user can act on."""
    if is_bitlocker(part):
        raise PlanError(
            "Windows is encrypted with BitLocker (Device Encryption), so LintabOS can't shrink it safely.\n"
            "In Windows: Settings > Privacy & security > Device encryption > turn it off, and wait until\n"
            "decryption finishes. Then run the installer again. (Suspending BitLocker is not enough.)")

    proc = run(["ntfsresize", "--info", "--no-progress-bar", part.path], check=False)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        low = out.lower()
        if "hibernat" in low or "fast restart" in low or "fast startup" in low:
            raise PlanError(
                "Windows is hibernated (Fast Startup), so its disk is not safe to modify.\n"
                "Boot Windows, open a Command Prompt as administrator, run:  powercfg /h off\n"
                "then choose Shut down (hold Shift while clicking it) and run the installer again.")
        if "check" in low or "dirty" in low or "inconsisten" in low or "unclean" in low:
            raise PlanError(
                "Windows flagged its disk for a consistency check.\n"
                "Boot Windows, choose Restart, then open a Command Prompt as administrator and run:  chkdsk C: /f\n"
                "(answer Y, restart, let it finish). If Windows only shows a recovery screen, use Troubleshoot → "
                "Command Prompt and run  manage-bde -unlock C: -rp <your 48-digit key>  before chkdsk.\n"
                "Then shut Windows down fully (hold Shift) and run the installer again.")
        raise PlanError(f"ntfsresize could not read the Windows volume:\n{out.strip()}")

    size = _SIZE_RE.search(out)
    minimum = _RESIZE_RE.search(out)
    if not size or not minimum:
        raise PlanError(f"Could not understand ntfsresize output:\n{out.strip()}")
    return NtfsInfo(size=int(size.group(1)), min_size=int(minimum.group(1)))


# --------------------------------------------------------------- dual-boot ---

def max_linux_size(windows: Partition, info: NtfsInfo) -> int:
    """Largest amount of space that can be taken from Windows."""
    keep = align_up(info.min_size + MIN_WINDOWS_HEADROOM, MiB)
    return max(0, align_down(windows.size - keep, MiB))


def plan_dualboot(disk: Disk, linux_size: int, windows: Optional[Partition] = None,
                  info: Optional[NtfsInfo] = None, check_esp: bool = True) -> Plan:
    if disk.label != "gpt":
        raise PlanError("This disk does not use a GPT partition table, so it can't be dual-booted in UEFI mode.")
    windows = windows or find_windows(disk)
    if windows is None:
        raise PlanError("No Windows installation was found on this disk.")
    esp = disk.find_esp()
    if esp is None:
        raise PlanError("Windows is installed but the EFI System Partition was not found.")
    if check_esp:
        if not esp_has_windows(esp):
            raise PlanError("The EFI partition does not contain the Windows boot manager; refusing to continue.")
        if esp_free_bytes(esp) < MIN_ESP_FREE:
            raise PlanError(
                "The Windows EFI partition has less than 16 MB free, not enough for the bootloader.")
    if any(p.mountpoints for p in disk.partitions if p is not esp):
        raise PlanError("A partition on this disk is mounted. Unmount it (or eject it in Files) first.")

    info = info or probe_ntfs(windows)
    linux_size = align_down(linux_size, MiB)
    allowed = max_linux_size(windows, info)
    if linux_size < MIN_LINUX_SIZE:
        raise PlanError(f"LintabOS needs at least {MIN_LINUX_SIZE // GiB} GB.")
    if linux_size > allowed:
        raise PlanError(
            f"Windows only has room to give up {allowed / GiB:.1f} GB "
            f"({linux_size / GiB:.1f} GB requested). Free up space in Windows or choose less.")

    new_windows_size = windows.size - linux_size
    linux_start = windows.start + new_windows_size
    root_guid = str(uuid.uuid4())
    backup = f"/tmp/lintab-gpt-backup-{os.path.basename(disk.path)}-{int(time.time())}.sfdisk"

    plan = Plan(disk=disk.path, mode="dualboot", root_partuuid=root_guid,
                esp_partuuid=esp.uuid, esp_is_new=False, windows_present=True, backup_path=backup)
    plan.summary = [
        f"Windows ({windows.path}): {windows.size / GiB:.1f} GB -> {new_windows_size / GiB:.1f} GB",
        f"LintabOS: new {linux_size / GiB:.1f} GB partition",
        f"Windows boot files in {esp.path} are kept; LintabOS adds its own folder there",
    ]
    plan.steps = [
        Step("Back up the partition table", ["sh", "-c", f"sfdisk --dump {disk.path} > {backup}"],
             destructive=False),
        # No --force anywhere: it would also switch off ntfsresize's own refusal to
        # touch hibernated/dirty volumes. The confirmation prompt is answered on stdin.
        Step("Test-run the Windows filesystem shrink (changes nothing)",
             ["ntfsresize", "--no-action", "--no-progress-bar", "--size", str(new_windows_size), windows.path],
             input="y\n", destructive=False),
        Step("Shrink the Windows filesystem",
             ["ntfsresize", "--no-progress-bar", "--size", str(new_windows_size), windows.path], input="y\n"),
        Step("Shrink the Windows partition to match",
             ["sfdisk", "--no-reread", "--no-tell-kernel", "-N", str(windows.number), disk.path],
             input=f",{new_windows_size // MiB}MiB\n"),
        Step("Create the LintabOS partition in the freed space",
             ["sfdisk", "--append", "--no-reread", "--no-tell-kernel", disk.path],
             input=(f"start={linux_start // MiB}MiB, size={linux_size // MiB}MiB, "
                    f"type={GUID_LINUX_ROOT_X86_64}, uuid={root_guid}, name=LintabOS\n")),
        *_settle_steps(disk.path),
        _mkfs_step(root_guid),
    ]
    return plan


# ------------------------------------------------------------ wipe / free ----

def _require_unmounted(disk: Disk) -> None:
    busy = [p.path for p in disk.partitions if p.mountpoints]
    if busy:
        raise PlanError(f"{', '.join(busy)} is in use. Unmount it (or eject it in Files) and try again.")


def plan_wipe(disk: Disk) -> Plan:
    _require_unmounted(disk)
    root_guid = str(uuid.uuid4())
    esp_guid = str(uuid.uuid4())
    script = (f"label: gpt\n"
              f"size={ESP_SIZE // MiB}MiB, type={GUID_ESP}, uuid={esp_guid}, name=EFI\n"
              f"type={GUID_LINUX_ROOT_X86_64}, uuid={root_guid}, name=LintabOS\n")
    plan = Plan(disk=disk.path, mode="wipe", root_partuuid=root_guid, esp_partuuid=esp_guid, esp_is_new=True)
    plan.summary = [f"EVERYTHING on {disk.display_name} will be erased",
                    f"New layout: {ESP_SIZE // MiB} MB EFI partition + LintabOS on the rest"]
    plan.steps = [
        Step("Erase old signatures", ["wipefs", "--all", "--force", disk.path]),
        Step("Write a new GPT partition table", ["sfdisk", "--no-reread", "--no-tell-kernel", disk.path], input=script),
        *_settle_steps(disk.path),
        Step("Format the EFI partition", ["mkfs.vfat", "-F", "32", "-n", "EFI", f"partuuid:{esp_guid}"]),
        _mkfs_step(root_guid),
    ]
    return plan


def plan_free_space(disk: Disk, region: FreeRegion, size: Optional[int] = None) -> Plan:
    if disk.label != "gpt":
        raise PlanError("This disk does not use a GPT partition table.")
    size = align_down(size if size is not None else region.size, MiB)
    if size < MIN_LINUX_SIZE:
        raise PlanError(f"LintabOS needs at least {MIN_LINUX_SIZE // GiB} GB of free space.")
    if size > region.size:
        raise PlanError("Not enough free space in that region.")

    _require_unmounted(disk)
    esp = disk.find_esp()
    root_guid = str(uuid.uuid4())
    plan = Plan(disk=disk.path, mode="free", root_partuuid=root_guid, windows_present=find_windows(disk) is not None)
    lines, start = [], region.start
    if esp is None:
        esp_guid = str(uuid.uuid4())
        lines.append(f"start={start // MiB}MiB, size={ESP_SIZE // MiB}MiB, type={GUID_ESP}, uuid={esp_guid}, name=EFI")
        start += ESP_SIZE
        size -= ESP_SIZE
        plan.esp_partuuid, plan.esp_is_new = esp_guid, True
    else:
        plan.esp_partuuid = esp.uuid
    lines.append(f"start={start // MiB}MiB, size={size // MiB}MiB, type={GUID_LINUX_ROOT_X86_64}, "
                 f"uuid={root_guid}, name=LintabOS")
    plan.summary = [f"LintabOS: new {size / GiB:.1f} GB partition in free space"]
    plan.steps = [
        Step("Create partitions in free space", ["sfdisk", "--append", "--no-reread", "--no-tell-kernel", disk.path],
             input="\n".join(lines) + "\n"),
        *_settle_steps(disk.path),
    ]
    if plan.esp_is_new:
        plan.steps.append(Step("Format the EFI partition",
                               ["mkfs.vfat", "-F", "32", "-n", "EFI", f"partuuid:{plan.esp_partuuid}"]))
    plan.steps.append(_mkfs_step(root_guid))
    return plan


def _settle_steps(disk: str) -> list[Step]:
    """Make the kernel pick up the new table. A shrunk partition must be resized
    in the kernel *before* a new one can overlap its old extent, hence update
    first, then add; partprobe is the fallback for kernels that refuse partx."""
    return [
        Step("Tell the kernel the resized partitions", ["partx", "--update", disk],
             destructive=False, allow_failure=True),
        Step("Tell the kernel the new partitions", ["partx", "--add", disk],
             destructive=False, allow_failure=True),
        Step("Re-read the partition table", ["partprobe", disk], destructive=False, allow_failure=True),
        Step("Wait for device nodes", ["udevadm", "settle", "--timeout=15"], destructive=False, allow_failure=True),
    ]


def _mkfs_step(root_guid: str) -> Step:
    return Step("Format the LintabOS partition (ext4)",
                ["mkfs.ext4", "-F", "-L", "LintabOS", f"partuuid:{root_guid}"])


# ----------------------------------------------------------------- execute ---

def resolve_partuuid(partuuid: str, disk: str, timeout: float = 15.0) -> str:
    """Device node of the partition with this GPT GUID on ``disk``. Reads the
    partition table itself (``sfdisk -J``) rather than udev's database, so it
    works before udev has caught up."""
    deadline = time.time() + timeout
    while True:
        dump = run(["sfdisk", "-J", disk], check=False).stdout
        if dump.strip():
            for entry in json.loads(dump)["partitiontable"].get("partitions", []):
                if entry.get("uuid", "").lower() == partuuid.lower() and os.path.exists(entry["node"]):
                    return entry["node"]
        if time.time() >= deadline:
            raise PlanError(f"The new partition {partuuid} never appeared; the disk layout may need a reboot.")
        run(["udevadm", "settle", "--timeout=3"], check=False)
        time.sleep(0.5)


def _resolve_argv(argv: list[str], disk: str) -> list[str]:
    """Replace ``partuuid:<guid>`` placeholders with real device paths. Plans are
    built before the partitions exist, so the device is only known at run time."""
    return [resolve_partuuid(a[len("partuuid:"):], disk) if a.startswith("partuuid:") else a for a in argv]


def apply_plan(plan: Plan, dry_run: bool = False,
               progress: Optional[Callable[[int, int, str], None]] = None) -> None:
    total = len(plan.steps)
    for i, step in enumerate(plan.steps, 1):
        if progress:
            progress(i, total, step.description)
        if dry_run:
            print(f"[dry-run] {step.description}: {' '.join(step.argv)}")
            continue
        argv = _resolve_argv(step.argv, plan.disk)
        proc = subprocess.run(argv, input=step.input, capture_output=True, text=True)
        if proc.returncode != 0 and not step.allow_failure:
            raise PlanError(
                f"Step failed: {step.description}\n{' '.join(argv)}\n"
                f"{(proc.stderr or proc.stdout).strip()}\n"
                + (f"\nThe partition table backup is at {plan.backup_path}" if plan.backup_path else ""))
