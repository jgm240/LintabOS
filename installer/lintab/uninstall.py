# SPDX-License-Identifier: MIT
"""Remove LintabOS and give its space back to Windows: the installer, in reverse.

Runs from the LintabOS live USB (the installed system can't delete the partition it is running from). It:

1. finds the LintabOS partition (GPT name or label "LintabOS", ext4) and deletes only that;
2. grows the Windows partition and its NTFS filesystem into the freed space, when the space sits right after Windows
   (it does when LintabOS was installed by our dual-boot installer) and Windows is not BitLocker-encrypted, hibernated
   or flagged for a disk check;
3. removes LintabOS's own folder (and the optional touch boot menu) from the EFI partition and its firmware boot
   entries, and puts "Windows Boot Manager" first again.

The rule the whole tool is built around: **it must leave Windows bootable**. So it refuses when no Windows is found on the
same disk (nothing would be left to boot), never touches Windows' boot files, writes a partition-table backup first, and
needs the confirmation phrase ``REMOVE-LINTABOS``. Windows runs a disk check on its next start after the NTFS resize; that
is normal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import bootmenu, disks, plan as planmod
from .disks import Disk, MiB, Partition
from .plan import PlanError, Step

LABEL = "LintabOS"
CONFIRM_PHRASE = "REMOVE-LINTABOS"
NVRAM_LABELS = ("LintabOS", bootmenu.ENTRY_LABEL)
REFIND_HEADER = "# LintabOS boot menu (rEFInd)"


@dataclass
class UninstallPlan:
    disk: str
    root: Partition
    windows: Partition
    esp: Partition
    new_windows_size: Optional[int] = None      # bytes; None when the freed space is left unallocated
    grow_skipped: str = ""                      # why it is left unallocated, for display
    steps: list[Step] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    backup_path: str = ""

    def as_plan(self) -> planmod.Plan:
        return planmod.Plan(disk=self.disk, mode="uninstall", steps=self.steps, summary=self.summary,
                            windows_present=True, backup_path=self.backup_path)

    def describe(self) -> str:
        return self.as_plan().describe()


def find_lintabos(disk: Disk) -> list[Partition]:
    """Partitions this tool may delete: ext4 and named/labelled LintabOS, with a Linux GPT type."""
    return [p for p in disk.partitions
            if p.fstype == "ext4" and LABEL in (p.name, p.label)
            and p.type_guid in (disks.GUID_LINUX_ROOT_X86_64, disks.GUID_LINUX_FS)]


def _next_start(disk: Disk, after: int, ignore: Partition) -> int:
    later = [p.start for p in disk.partitions if p is not ignore and p.start >= after]
    return min(later) if later else disk.last_usable


def plan_uninstall(disk: Disk, windows: Optional[Partition] = None,
                   probe: Callable[[Partition], planmod.NtfsInfo] = planmod.probe_ntfs,
                   check_esp: bool = True) -> UninstallPlan:
    if disk.label != "gpt":
        raise PlanError("This disk does not use a GPT partition table; LintabOS was not installed here by this installer.")
    roots = find_lintabos(disk)
    if not roots:
        raise PlanError("No LintabOS partition was found on this disk.")
    if len(roots) > 1:
        raise PlanError("More than one LintabOS partition was found. This tool removes exactly one; "
                        "remove the others by hand so nothing is deleted by mistake.")
    root = roots[0]

    windows = windows or disks.find_windows(disk)
    if windows is None:
        raise PlanError(
            "No Windows installation was found on this disk. Removing LintabOS would leave nothing on it that can start, "
            "so this tool won't do that. (To wipe the disk and reinstall Windows, use Windows' own installer.)")
    esp = disk.find_esp()
    if esp is None:
        raise PlanError("The EFI System Partition was not found; refusing to continue.")
    if check_esp and not disks.esp_has_windows(esp):
        raise PlanError("The EFI partition does not contain the Windows boot manager; refusing to continue.")
    busy = [p for p in disk.partitions if p.mountpoints]
    if busy:
        if root in busy:
            raise PlanError("LintabOS is running from this partition right now. Start the computer from the LintabOS USB "
                            "stick (the live system) and run Remove LintabOS from there.")
        raise PlanError("A partition on this disk is mounted. Unmount it (or eject it in Files) first.")

    # Where could Windows grow to? Only into free space that begins right after it.
    result = UninstallPlan(disk=disk.path, root=root, windows=windows, esp=esp)
    limit = _next_start(disk, root.end, root)
    adjacent = windows.end <= root.start and root.start - windows.end < MiB and root.end <= limit
    new_size = disks.align_down(limit, MiB) - windows.start
    if not adjacent or new_size <= windows.size:
        result.grow_skipped = ("the freed space does not sit directly after Windows' partition (Windows' Disk Management "
                               "can extend it if there is free space next to it)")
    elif disks.is_bitlocker(windows):
        result.grow_skipped = ("Windows' drive is BitLocker-encrypted, so it can't be resized from here (the space stays "
                               "unallocated for Windows to use)")
    else:
        try:
            probe(windows)
            result.new_windows_size = new_size
        except PlanError as exc:
            result.grow_skipped = ("Windows is not in a state where its disk can be safely resized: "
                                   + str(exc).splitlines()[0])

    backup = f"/tmp/lintab-gpt-backup-before-removal-{os.path.basename(disk.path)}.sfdisk"
    result.backup_path = backup
    gib = (root.size) / disks.GiB
    result.summary = [
        f"Delete the LintabOS partition ({root.path}, {gib:.1f} GB). Nothing else on the disk is deleted.",
        (f"Give the space back to Windows ({windows.path}): {windows.size / disks.GiB:.1f} GB -> "
         f"{result.new_windows_size / disks.GiB:.1f} GB" if result.new_windows_size
         else f"The space stays unallocated: {result.grow_skipped}."),
        f"Remove LintabOS's folder and boot entries; Windows' boot files in {esp.path} are not touched",
        "Windows will run a disk check the next time it starts after being resized; that is normal.",
    ]
    steps = [
        Step("Back up the partition table", ["sh", "-c", f"sfdisk --dump {disk.path} > {backup}"], destructive=False),
        Step("Delete the LintabOS partition",
             ["sfdisk", "--no-reread", "--no-tell-kernel", "--delete", disk.path, str(root.number)]),
        Step("Tell the kernel the partition is gone", ["partx", "--delete", "--nr", str(root.number), disk.path],
             destructive=False, allow_failure=True),
    ]
    if result.new_windows_size:
        steps += [
            Step("Grow the Windows partition into the freed space",
                 ["sfdisk", "--no-reread", "--no-tell-kernel", "-N", str(windows.number), disk.path],
                 input=f",{result.new_windows_size // MiB}MiB\n"),
            Step("Tell the kernel the resized partition", ["partx", "--update", disk.path],
                 destructive=False, allow_failure=True),
            Step("Re-read the partition table", ["partprobe", disk.path], destructive=False, allow_failure=True),
            Step("Wait for device nodes", ["udevadm", "settle", "--timeout=15"], destructive=False, allow_failure=True),
            Step("Test-run growing the Windows filesystem (changes nothing)",
                 ["ntfsresize", "--no-action", "--no-progress-bar", windows.path], input="y\n", destructive=False),
            Step("Grow the Windows filesystem to fill the partition",
                 ["ntfsresize", "--no-progress-bar", windows.path], input="y\n"),
        ]
    else:
        steps += [Step("Re-read the partition table", ["partprobe", disk.path], destructive=False, allow_failure=True)]
    result.steps = steps
    return result


def _default_run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def clean_efi_partition(esp_mount: str) -> list[str]:
    """Remove LintabOS's own folders from a mounted EFI partition. Windows' folders are never named here."""
    removed = []
    target = os.path.join(esp_mount, "EFI", "LintabOS")
    if os.path.isdir(target):
        shutil.rmtree(target)
        removed.append("EFI/LintabOS")
    conf = os.path.join(esp_mount, "EFI", "refind", "refind.conf")
    try:
        with open(conf) as f:
            ours = f.readline().startswith(REFIND_HEADER)
    except OSError:
        ours = False
    if ours:                                    # a rEFInd folder is only ours if our menu wrote its config
        shutil.rmtree(os.path.join(esp_mount, "EFI", "refind"))
        removed.append("EFI/refind")
    return removed


def fix_boot_entries(run: Callable = _default_run) -> list[str]:
    """Delete LintabOS's firmware boot entries; make Windows Boot Manager first. Returns warnings."""
    listing = run(["efibootmgr"])
    if getattr(listing, "returncode", 0) != 0:
        return ["The firmware boot entries could not be read (not booted in UEFI mode?). Windows is still on the EFI "
                "partition; if the computer doesn't start it, pick Windows Boot Manager in the firmware boot menu."]
    order, entries = bootmenu.parse_efibootmgr(listing.stdout)
    warnings = []
    for boot_id, label in entries.items():
        if label in NVRAM_LABELS:
            run(["efibootmgr", "-B", "-b", boot_id])
            order = [b for b in order if b != boot_id]
    windows_ids = [i for i, label in entries.items() if label.lower().startswith("windows boot manager")]
    if windows_ids:
        first = windows_ids[0]
        order = [first] + [b for b in order if b != first]
    else:
        warnings.append("Windows Boot Manager has no firmware boot entry; the firmware may start it from its fallback path.")
    if order:
        run(["efibootmgr", "-o", ",".join(order)])
    return warnings


def apply_uninstall(plan: UninstallPlan, progress: Optional[Callable[[int, int, str], None]] = None,
                    run: Callable = _default_run, dry_run: bool = False) -> list[str]:
    """Do it. Returns warnings (things that need the user's attention but didn't stop the removal)."""
    planmod.apply_plan(plan.as_plan(), dry_run=dry_run, progress=progress)
    if dry_run:
        return []
    warnings: list[str] = []
    mnt = tempfile.mkdtemp(prefix="lintab-esp-")
    try:
        mounted = run(["mount", plan.esp.path, mnt])
        if getattr(mounted, "returncode", 0) != 0:
            warnings.append(f"Could not open the EFI partition to remove LintabOS's folder ({plan.esp.path}). "
                            "It is harmless; Windows is unaffected.")
        else:
            try:
                clean_efi_partition(mnt)
            finally:
                run(["umount", mnt])
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass
    warnings += fix_boot_entries(run)
    return warnings
