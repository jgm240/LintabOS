# SPDX-License-Identifier: MIT
"""disks.run() finds its tools even when PATH doesn't include /sbin or /usr/sbin — the real bug a user hit: LinWinMod's
disk scan runs unprivileged (unlike the installer and Remove LintabOS, which always elevate first, so sudo/pkexec's
"secure_path" normally adds those directories for free), and a plain desktop session's PATH often excludes them, so a
genuinely installed tool like sfdisk could not be found."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import disks  # noqa: E402


def fake_which(name, path=None):
    """Only finds things that are "really there": in /usr/bin always, or in /usr/sbin when the given path covers it."""
    locations = {"sfdisk": "/usr/sbin/sfdisk", "blkid": "/sbin/blkid", "ls": "/usr/bin/ls"}
    target = locations.get(name)
    if target is None:
        return None
    directory = os.path.dirname(target)
    search = (path or "").split(os.pathsep)
    return target if directory in search else None


def test_a_tool_on_the_inherited_path_is_used_directly():
    assert disks.resolve_executable("ls", which=fake_which, environ={"PATH": "/usr/bin"}) == "/usr/bin/ls"


def test_a_sbin_only_tool_is_still_found_when_the_inherited_path_excludes_sbin():
    """This is the exact failure: PATH=/usr/local/bin:/usr/bin:/bin (a typical desktop session) has no /usr/sbin."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    assert disks.resolve_executable("sfdisk", which=fake_which, environ=env) == "/usr/sbin/sfdisk"
    assert disks.resolve_executable("blkid", which=fake_which, environ=env) == "/sbin/blkid"


def test_a_tool_that_genuinely_does_not_exist_is_returned_unchanged():
    """So the eventual error is the familiar "no such file or directory" from actually trying to run it, not a
    confusing one from this function pretending it knows better."""
    assert disks.resolve_executable("not-a-real-tool", which=fake_which, environ={"PATH": ""}) == "not-a-real-tool"


def test_a_path_that_already_contains_a_slash_is_left_alone():
    """Only bare command names are resolved; an explicit path (relative or absolute) is the caller's own choice."""
    assert disks.resolve_executable("/opt/weird/sfdisk", which=fake_which) == "/opt/weird/sfdisk"
    assert disks.resolve_executable("./sfdisk", which=fake_which) == "./sfdisk"


def test_run_resolves_only_the_executable_and_leaves_the_arguments_alone(monkeypatch):
    calls = []

    def fake_subprocess_run(argv, **kwargs):
        calls.append(argv)
        import subprocess
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(disks.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(disks, "resolve_executable", lambda name, **k: f"/usr/sbin/{name}")
    disks.run(["sfdisk", "-J", "/dev/sda", "--no-reread"])
    assert calls == [["/usr/sbin/sfdisk", "-J", "/dev/sda", "--no-reread"]]


def test_run_with_an_empty_argv_does_not_crash(monkeypatch):
    monkeypatch.setattr(disks.subprocess, "run", lambda argv, **k: __import__("subprocess").CompletedProcess(argv, 0, "", ""))
    disks.run([], check=False)


def test_windows_partitions_reads_the_uuid_through_the_same_path_safe_run():
    """windows_partitions() is exactly what LinWinMod's unprivileged scan calls; its blkid lookup must go through
    disks.run() (and therefore get the sbin fallback) rather than a bare subprocess.run()."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "installer/lintab/winfiles.py")).read()
    assert 'subprocess.run(["blkid"' not in source
    assert 'disks.run(["blkid"' in source


# ------------------------------------------------------------------- ESP mount reuse (real Duet 3 bug) --
# Found live: the ESP is always already mounted at /boot/efi on any real *installed* LintabOS system (its own
# GRUB lives there too, on the same shared ESP as Windows). esp_has_windows()/esp_free_bytes() used to always try
# a fresh mount regardless, which the kernel refuses ("Can't mount, would change RO state") - so they silently
# returned "no Windows here" / 0 bytes free on every real install, never because anything was actually wrong.

def _fake_esp(path="/dev/fake-esp1"):
    class Esp:
        pass
    e = Esp()
    e.path = path
    return e


def test_mount_point_of_finds_an_existing_mount(tmp_path):
    proc_mounts = tmp_path / "mounts"
    proc_mounts.write_text("/dev/nvme0n1p1 /boot/efi vfat rw,relatime 0 0\n/dev/nvme0n1p5 / ext4 rw 0 0\n")
    assert disks.mount_point_of("/dev/nvme0n1p1", str(proc_mounts)) == "/boot/efi"
    assert disks.mount_point_of("/dev/nowhere", str(proc_mounts)) is None


def test_lsblk_fallback_partitions_reads_type_uuid_name_fstype_label_without_start(monkeypatch):
    """Matches real output captured live from a Duet 3 running unprivileged: sfdisk can't open the raw device
    ("Permission denied" outside the `disk` group), but lsblk's own udev-sourced columns still have everything
    except a start offset, which nothing unprivileged needs anyway."""
    node = {
        "name": "nvme0n1", "path": "/dev/nvme0n1", "size": 256060514304, "pttype": "gpt",
        "children": [
            {"name": "nvme0n1p3", "path": "/dev/nvme0n1p3", "size": 198389530624, "fstype": "ntfs",
             "label": "Windows-SSD", "mountpoints": [],
             "parttype": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
             "partuuid": "b3216a56-55b7-4918-9bde-b845cf3aaf37", "partlabel": "Basic data partition", "partn": 3},
            {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "size": 16777216, "fstype": None, "label": None,
             "mountpoints": [], "parttype": None, "partuuid": None, "partlabel": None, "partn": None},
        ],
    }
    found = disks._lsblk_fallback_partitions(node, sector=512)
    assert len(found) == 1                                  # the MSR (no parttype) is correctly skipped
    part = found[0]
    assert part.path == "/dev/nvme0n1p3" and part.number == 3 and part.start == 0
    assert part.type_guid == "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
    assert part.uuid == "b3216a56-55b7-4918-9bde-b845cf3aaf37"
    assert part.fstype == "ntfs" and part.label == "Windows-SSD" and part.is_ms_data


def test_esp_has_windows_reuses_an_already_mounted_esp_instead_of_remounting(monkeypatch, tmp_path):
    already_mounted = tmp_path / "boot-efi"
    (already_mounted / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (already_mounted / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").write_bytes(b"fake")

    monkeypatch.setattr(disks, "mount_point_of",
                        lambda device, proc_mounts="/proc/mounts": str(already_mounted) if device == "/dev/fake-esp1" else None)

    mount_calls = []
    monkeypatch.setattr(disks, "run", lambda argv, **k: mount_calls.append(argv))

    assert disks.esp_has_windows(_fake_esp()) is True
    assert mount_calls == []          # never attempted to mount or unmount an already-mounted ESP
