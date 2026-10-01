# SPDX-License-Identifier: MIT
"""End-to-end partitioner tests against loop-device disk images.

These build a fake Windows tablet disk (ESP + MSR + NTFS C: + WinRE), run the
real dual-boot plan with ntfsresize/sfdisk, then verify that Windows' files and
boot partition survived. They need root, loop devices and real NTFS tools, so
they run inside the builder container:  ./scripts/test-partitioner.sh
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import disks, plan as planmod  # noqa: E402
from lintab.disks import GiB, MiB  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("ntfsresize") or not shutil.which("losetup"),
    reason="needs root, loop devices and ntfs-3g",
)


def sh(*argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw).stdout


@pytest.fixture(autouse=True)
def small_limits(monkeypatch):
    monkeypatch.setattr(planmod, "MIN_LINUX_SIZE", 1 * GiB)
    monkeypatch.setattr(planmod, "MIN_WINDOWS_HEADROOM", 256 * MiB)


@pytest.fixture(params=[512, 4096], ids=["512b-sectors", "4k-sectors-like-UFS"])
def windows_disk(request, tmp_path):
    sector = request.param
    img = tmp_path / "disk.img"
    with open(img, "wb") as f:
        f.truncate(8 * GiB)
    loop = sh("losetup", "--find", "--show", "--partscan", "--sector-size", str(sector), str(img)).strip()
    try:
        script = (
            "label: gpt\n"
            f"size=100MiB, type={disks.GUID_ESP}, name=EFI\n"
            f"size=16MiB, type={disks.GUID_MS_RESERVED}, name=MSR\n"
            f"size=6GiB, type={disks.GUID_MS_BASIC_DATA}, name=Windows\n"
            f"size=768MiB, type={disks.GUID_WIN_RE}, name=Recovery\n"
        )
        sh("sfdisk", loop, input=script)  # partscan: the kernel re-reads the table itself
        parts = {n: f"{loop}p{n}" for n in (1, 2, 3, 4)}
        for p in parts.values():
            for _ in range(50):
                if os.path.exists(p):
                    break
                subprocess.run(["sleep", "0.1"])
            assert os.path.exists(p), f"{p} did not appear"

        sh("mkfs.vfat", "-F", "32", parts[1])
        sh("mkfs.ntfs", "-Q", "-F", "-L", "Windows", parts[3])
        sh("mkfs.ntfs", "-Q", "-F", "-L", "Recovery", parts[4])

        mnt = tempfile.mkdtemp()
        sh("mount", parts[1], mnt)
        os.makedirs(f"{mnt}/EFI/Microsoft/Boot")
        open(f"{mnt}/EFI/Microsoft/Boot/bootmgfw.efi", "wb").write(os.urandom(4096))
        sh("umount", mnt)

        sh("ntfs-3g", parts[3], mnt)
        os.makedirs(f"{mnt}/Windows/System32")
        payload = os.urandom(24 * MiB)
        open(f"{mnt}/Windows/System32/payload.bin", "wb").write(payload)
        sh("umount", mnt)

        yield {"loop": loop, "parts": parts, "sha": hashlib.sha256(payload).hexdigest(), "sector": sector}
    finally:
        subprocess.run(["losetup", "-d", loop], capture_output=True)


def test_reads_windows_layout(windows_disk):
    d = disks.read_disk(windows_disk["loop"])
    assert d.label == "gpt" and d.sector_size == windows_disk["sector"]
    win = disks.find_windows(d)
    assert win is not None and win.number == 3
    assert d.find_esp().number == 1
    assert disks.esp_has_windows(d.find_esp())


def test_dualboot_shrinks_windows_and_keeps_data(windows_disk):
    loop = windows_disk["loop"]
    d = disks.read_disk(loop)
    win = disks.find_windows(d)
    before = {p.number: (p.start, p.type_guid, p.uuid, p.name) for p in d.partitions}

    plan = planmod.plan_dualboot(d, 2 * GiB)
    text = plan.describe()
    assert "ntfsresize --no-action" in text  # always test-runs first
    planmod.apply_plan(plan)

    d2 = disks.read_disk(loop)
    by_num = {p.number: p for p in d2.partitions}
    assert len(by_num) == 5

    # Windows partition: same start/type/GUID/name, exactly 2 GiB smaller.
    w = by_num[3]
    assert (w.start, w.type_guid, w.uuid, w.name) == before[3]
    assert w.size == win.size - 2 * GiB

    # Everything else untouched.
    for n in (1, 2, 4):
        assert (by_num[n].start, by_num[n].type_guid, by_num[n].uuid, by_num[n].name) == before[n]

    # New Linux partition sits right after Windows, before the recovery partition.
    root = [p for p in d2.partitions if p.uuid == plan.root_partuuid][0]
    assert root.start == w.end and root.size == 2 * GiB and root.type_guid == disks.GUID_LINUX_ROOT_X86_64
    assert root.end <= by_num[4].start and root.fstype == "ext4"

    # NTFS is consistent at the new size and the data is intact. ntfsresize
    # deliberately sets the "check disk" flag so Windows runs chkdsk on its next
    # boot, hence --force here and ntfsfix as the independent consistency check.
    sh("ntfsresize", "--info", "--force", "--no-progress-bar", w.path)
    sh("ntfsfix", "--no-action", w.path)
    mnt = tempfile.mkdtemp()
    sh("ntfs-3g", "-o", "ro", w.path, mnt)
    try:
        digest = hashlib.sha256(open(f"{mnt}/Windows/System32/payload.bin", "rb").read()).hexdigest()
    finally:
        sh("umount", mnt)
    assert digest == windows_disk["sha"]
    assert os.path.exists(plan.backup_path) and "label: gpt" in open(plan.backup_path).read()


def test_refuses_to_take_too_much(windows_disk):
    d = disks.read_disk(windows_disk["loop"])
    with pytest.raises(planmod.PlanError, match="only has room"):
        planmod.plan_dualboot(d, disks.find_windows(d).size - 100 * MiB)


def test_bitlocker_is_refused(windows_disk):
    loop = windows_disk["loop"]
    part = windows_disk["parts"][3]
    with open(part, "r+b") as dev:
        dev.seek(3)
        dev.write(b"-FVE-FS-")
    d = disks.read_disk(loop)
    with pytest.raises(planmod.PlanError, match="BitLocker"):
        planmod.plan_dualboot(d, 2 * GiB)


def test_wipe_plan_creates_esp_and_root(windows_disk):
    d = disks.read_disk(windows_disk["loop"])
    plan = planmod.plan_wipe(d)
    planmod.apply_plan(plan)
    d2 = disks.read_disk(windows_disk["loop"])
    assert [p.fstype for p in d2.partitions] == ["vfat", "ext4"]
    assert d2.partitions[0].is_esp


def test_second_shrink_is_refused_until_windows_checked_its_disk(windows_disk):
    """ntfsresize flags the volume for chkdsk; shrinking it again before Windows
    has run that check must be refused with instructions, not forced through."""
    d = disks.read_disk(windows_disk["loop"])
    planmod.apply_plan(planmod.plan_dualboot(d, 1 * GiB))
    d2 = disks.read_disk(windows_disk["loop"])
    with pytest.raises(planmod.PlanError, match="chkdsk"):
        planmod.plan_dualboot(d2, 1 * GiB)
