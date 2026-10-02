# SPDX-License-Identifier: MIT
"""Safety rules of "Remove LintabOS": what it will and will not delete, and when it leaves the space unallocated.

Pure logic on synthetic disks (no devices). The real thing, on a loop-device disk, is in test_partitioner_loop.py."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import bootmenu, disks, plan as planmod, uninstall  # noqa: E402
from lintab.disks import Disk, GiB, MiB, Partition  # noqa: E402


def part(number, start, size, type_guid, name="", fstype="", label="", mounts=()):
    return Partition(number=number, path=f"/dev/sdz{number}", start=start * MiB, size=size * MiB, type_guid=type_guid,
                     uuid=f"guid-{number}", name=name, fstype=fstype, label=label, mountpoints=list(mounts))


def layout(root_start=7000, root_size=2000, recovery_start=9000, **root_kw):
    parts = [
        part(1, 1, 100, disks.GUID_ESP, "EFI", "vfat"),
        part(2, 101, 16, disks.GUID_MS_RESERVED),
        part(3, 117, root_start - 117, disks.GUID_MS_BASIC_DATA, "Windows", "ntfs"),
        part(5, root_start, root_size, disks.GUID_LINUX_ROOT_X86_64, "LintabOS", "ext4", "LintabOS", **root_kw),
    ]
    if recovery_start:
        parts.append(part(4, recovery_start, 768, disks.GUID_WIN_RE, "Recovery", "ntfs"))
    return Disk(path="/dev/sdz", size=12 * GiB, sector_size=512, model="Test", transport="usb", removable=False,
                label="gpt", first_usable=MiB, last_usable=12 * GiB - MiB, partitions=parts)


INFO = lambda p: planmod.NtfsInfo(size=p.size, min_size=1 * GiB)


def build(disk, **kw):
    kw.setdefault("check_esp", False)
    kw.setdefault("probe", INFO)
    kw.setdefault("windows", next(p for p in disk.partitions if p.number == 3))
    return uninstall.plan_uninstall(disk, **kw)


def argvs(plan):
    return [" ".join(s.argv) for s in plan.steps]


def test_it_deletes_exactly_the_lintabos_partition_and_grows_windows_into_the_gap():
    plan = build(layout())
    assert plan.root.number == 5 and plan.new_windows_size == (9000 - 117) * MiB   # up to the recovery partition
    cmds = argvs(plan)
    assert any("--delete /dev/sdz 5" in c for c in cmds)
    assert [s for s in plan.steps if s.argv[0] == "sfdisk" and "--delete" in s.argv][0].argv[-1] == "5"
    grow = [s for s in plan.steps if "-N" in s.argv][0]
    assert grow.argv[grow.argv.index("-N") + 1] == "3" and grow.input == f",{9000 - 117}MiB\n"
    assert cmds.index(next(c for c in cmds if "ntfsresize --no-action" in c)) < cmds.index(
        next(c for c in cmds if c.startswith("ntfsresize --no-progress-bar")))          # test-run precedes the real resize
    assert cmds[0].startswith("sh -c sfdisk --dump") and plan.steps[0].destructive is False   # backup first
    touched = {s.argv[-1] for s in plan.steps if s.argv[0] == "sfdisk" and "--delete" in s.argv}
    assert touched == {"5"}                                                              # nothing else is ever deleted


def test_when_lintabos_is_the_last_partition_windows_grows_to_the_end_of_the_disk():
    plan = build(layout(recovery_start=0))
    assert plan.new_windows_size is not None and plan.new_windows_size > (7000 - 117) * MiB


def test_it_refuses_when_no_windows_is_on_the_disk():
    disk = layout()
    disk.partitions = [p for p in disk.partitions if p.number not in (2, 3)]
    with pytest.raises(planmod.PlanError, match="No Windows installation"):
        uninstall.plan_uninstall(disk, check_esp=False, probe=INFO)


def test_it_only_touches_partitions_it_can_positively_identify():
    disk = layout()
    for p in disk.partitions:
        if p.number == 5:
            p.name, p.label = "Data", "Data"                      # an ordinary Linux partition: not ours
    with pytest.raises(planmod.PlanError, match="No LintabOS partition"):
        build(disk)
    disk = layout()
    disk.partitions[3].fstype = "btrfs"                         # right name, wrong filesystem
    with pytest.raises(planmod.PlanError, match="No LintabOS partition"):
        build(disk)
    disk = layout()
    disk.partitions.append(part(6, 9800, 500, disks.GUID_LINUX_ROOT_X86_64, "LintabOS", "ext4", "LintabOS"))
    with pytest.raises(planmod.PlanError, match="More than one"):
        build(disk)


def test_it_will_not_remove_the_system_it_is_running_from():
    with pytest.raises(planmod.PlanError, match="live system"):
        build(layout(mounts=["/"]))
    disk = layout()
    disk.partitions[0].mountpoints = ["/mnt"]
    with pytest.raises(planmod.PlanError, match="mounted"):
        build(disk)


def test_space_is_left_unallocated_rather_than_risking_windows():
    # BitLocker: no filesystem resize from Linux
    original = disks.is_bitlocker
    disks.is_bitlocker = lambda p: p.number == 3
    try:
        plan = build(layout())
    finally:
        disks.is_bitlocker = original
    assert plan.new_windows_size is None and "BitLocker" in plan.grow_skipped
    assert not any("ntfsresize" in c or "-N" in c for c in argvs(plan)) and any("--delete" in c for c in argvs(plan))
    # Windows hibernated / flagged: the probe raises
    def dirty(_p):
        raise planmod.PlanError("Windows is hibernated (Fast Startup), so its disk is not safe to modify.\nmore")
    plan = build(layout(), probe=dirty)
    assert plan.new_windows_size is None and "hibernated" in plan.grow_skipped
    assert not any("ntfsresize" in c for c in argvs(plan))
    # LintabOS is not directly after Windows (a partition sits between): nothing to grow into
    disk = layout()
    disk.partitions.append(part(6, 7000 - 600, 500, disks.GUID_MS_BASIC_DATA, "Other", "ntfs"))
    disk.partitions[2].size = (7000 - 600 - 117 - 1) * MiB
    plan = build(disk)
    assert plan.new_windows_size is None and "directly after" in plan.grow_skipped


# ---------------------------------------------------------------- EFI + firmware --

def test_efi_cleanup_removes_only_lintabos_folders(tmp_path):
    esp = tmp_path
    for rel in ("EFI/LintabOS/grubx64.efi", "EFI/Microsoft/Boot/bootmgfw.efi", "EFI/BOOT/BOOTX64.EFI", "EFI/Dell/x.efi"):
        (esp / rel).parent.mkdir(parents=True, exist_ok=True)
        (esp / rel).write_bytes(b"MZ")
    (esp / "EFI/refind").mkdir()
    (esp / "EFI/refind/refind.conf").write_text(uninstall.REFIND_HEADER + ". Generated\n")
    assert sorted(uninstall.clean_efi_partition(str(esp))) == ["EFI/LintabOS", "EFI/refind"]
    for kept in ("EFI/Microsoft/Boot/bootmgfw.efi", "EFI/BOOT/BOOTX64.EFI", "EFI/Dell/x.efi"):
        assert (esp / kept).exists()
    assert not (esp / "EFI/LintabOS").exists() and not (esp / "EFI/refind").exists()


def test_someone_elses_refind_folder_is_left_alone(tmp_path):
    (tmp_path / "EFI/refind").mkdir(parents=True)
    (tmp_path / "EFI/refind/refind.conf").write_text("# my own config\n")
    assert uninstall.clean_efi_partition(str(tmp_path)) == [] and (tmp_path / "EFI/refind/refind.conf").exists()


class Firmware:
    def __init__(self, order, entries, ok=True):
        self.order, self.entries, self.ok, self.calls = order, entries, ok, []

    def __call__(self, argv):
        import subprocess
        self.calls.append(argv)
        if not self.ok:
            return subprocess.CompletedProcess(argv, 2, "", "EFI variables are not supported")
        if argv[:2] == ["efibootmgr", "-B"]:
            self.entries.pop(argv[3], None)
        elif argv[:2] == ["efibootmgr", "-o"]:
            self.order = argv[2].split(",")
        text = f"BootOrder: {','.join(self.order)}\n" + "".join(f"Boot{i}* {l}\n" for i, l in self.entries.items())
        return subprocess.CompletedProcess(argv, 0, text, "")


def test_boot_entries_are_cleaned_and_windows_goes_first():
    fw = Firmware(["0003", "0001", "0000"], {"0000": "Windows Boot Manager", "0001": "LintabOS", "0003": bootmenu.ENTRY_LABEL,
                                             "0004": "UEFI: USB stick"})
    assert uninstall.fix_boot_entries(fw) == []
    assert fw.order == ["0000"] and set(fw.entries) == {"0000", "0004"}


def test_no_windows_entry_or_no_efi_variables_gives_a_warning_not_a_crash():
    fw = Firmware(["0001"], {"0001": "LintabOS"})
    assert "Windows Boot Manager has no firmware boot entry" in uninstall.fix_boot_entries(fw)[0]
    assert "could not be read" in uninstall.fix_boot_entries(Firmware([], {}, ok=False))[0]


def test_the_confirmation_phrase_is_required(monkeypatch, capsys):
    from lintab import uninstall_cli
    monkeypatch.setattr(uninstall_cli, "_plan", lambda args: build(layout()))
    monkeypatch.setattr(uninstall, "apply_uninstall", lambda *a, **k: pytest.fail("must not write without confirmation"))
    assert uninstall_cli.main(["apply", "/dev/sdz"]) == 3
    assert "REMOVE-LINTABOS" in capsys.readouterr().err


def test_a_disk_discovery_failure_is_a_plain_message_not_a_traceback(monkeypatch, capsys):
    from lintab import uninstall_cli

    def broken():
        raise disks.DiskError("lsblk failed (32): not a block device")
    monkeypatch.setattr(uninstall_cli.disks, "list_disks", broken)
    for command in (["list"], ["plan"], ["apply", "--dry-run"]):
        assert uninstall_cli.main(command) == 2
    assert "could not read the disks" in capsys.readouterr().err
