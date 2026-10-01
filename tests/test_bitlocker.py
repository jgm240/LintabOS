# SPDX-License-Identifier: MIT
"""BitLocker unlock + in-place decrypt, tested against the real `dislocker`.

Volumes come from tests/bitlocker_image.py (a generator checked against real dislocker). The decrypt
tests include simulated power loss at every journal/commit boundary: after each crash a second run must
resume and the result must be byte-identical to the original NTFS volume, with Windows' boot files intact.
Needs root, /dev/fuse and dislocker: run via scripts/test-bitlocker.sh.
"""

import hashlib
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))
sys.path.insert(0, os.path.dirname(__file__))

import bitlocker_image as bli  # noqa: E402
from lintab import bitlocker, bitlocker_decrypt as bd  # noqa: E402

MiB = 1024 * 1024
needs_env = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("dislocker-fuse") or not shutil.which("ntfs-3g"),
    reason="needs root, dislocker and ntfs-3g",
)
PASSWORD = bli.make_recovery_password(7)
WINDOWS_FILES = {
    "Windows/System32/winload.efi": 2 * MiB, "Windows/System32/ntoskrnl.exe": 3 * MiB,
    "Windows/System32/config/SYSTEM": 3 * MiB, "Windows/System32/config/SOFTWARE": 2 * MiB,
    "Users/test/data.bin": 3_000_000,
}


def sh(*argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw).stdout


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """A 32 MiB image: 28 MiB of NTFS holding Windows-like files, then zero padding."""
    d = tmp_path_factory.mktemp("bl")
    path = str(d / "plain.img")
    with open(path, "wb") as f:
        f.truncate(28 * MiB)
    sh("mkfs.ntfs", "-Q", "-F", "-s", "512", "-c", "4096", "-L", "Windows", path)
    mnt = str(d / "mnt")
    os.makedirs(mnt)
    sh("ntfs-3g", path, mnt)
    try:
        for rel, size in WINDOWS_FILES.items():
            full = os.path.join(mnt, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(os.urandom(size))
        with open(os.path.join(mnt, "Users/test/hello.txt"), "w") as f:
            f.write("hello from windows\n")
    finally:
        sh("umount", mnt)
    with open(path, "r+b") as f:
        f.truncate(32 * MiB)
    return path


@pytest.fixture(scope="module", params=[bli.AES_XTS_128, bli.AES_128_CBC], ids=["xts128", "cbc128"])
def encrypted(request, plain, tmp_path_factory):
    path = str(tmp_path_factory.mktemp("enc") / "bl.img")
    bli.build(plain, path, PASSWORD, method=request.param)
    return path


@pytest.fixture
def disk(encrypted, tmp_path):
    """A fresh writable copy of the encrypted volume (decrypting modifies it)."""
    copy = str(tmp_path / "device.img")
    shutil.copyfile(encrypted, copy)
    yield copy
    bitlocker.lock_all()


@pytest.fixture
def journal_root(tmp_path):
    root = tmp_path / "esp"
    root.mkdir()
    return str(root)


# ------------------------------------------------------------ recovery keys --

def test_valid_key_is_normalized():
    assert bitlocker.normalize_recovery_key(PASSWORD.replace("-", " ")) == PASSWORD
    assert bitlocker.normalize_recovery_key(PASSWORD.replace("-", "")) == PASSWORD


@pytest.mark.parametrize("bad,why", [
    ("", "Enter"), ("12345", "48 digits"), ("x" * 48, "digits"),
    ("000001" + PASSWORD.replace("-", "")[6:], "Group 1"),            # not divisible by 11
])
def test_bad_keys_get_clear_messages(bad, why):
    with pytest.raises(bitlocker.BitLockerError, match=why) as exc:
        bitlocker.normalize_recovery_key(bad)
    assert exc.value.kind == "key_format"


def test_block_too_large_is_rejected():
    # 999999 = 11 * 90909: divisible by 11 (and so passes the check digit) but above 65535 * 11
    with pytest.raises(bitlocker.BitLockerError, match="Group 3"):
        bitlocker.normalize_recovery_key("-".join(PASSWORD.split("-")[:2] + ["999999"] + PASSWORD.split("-")[3:]))


# ------------------------------------------------------------------- unlock --

@needs_env
def test_unlock_reads_windows_files(disk, plain, tmp_path):
    handle = bitlocker.unlock(disk, PASSWORD)
    mnt = bitlocker.mount_view(handle, str(tmp_path / "view"))
    assert open(os.path.join(mnt, "Users/test/hello.txt")).read() == "hello from windows\n"
    want = hashlib.sha256(_read_plain_file(plain, "Users/test/data.bin", tmp_path)).hexdigest()
    got = hashlib.sha256(open(os.path.join(mnt, "Users/test/data.bin"), "rb").read()).hexdigest()
    assert got == want
    bitlocker.lock(disk)
    assert not os.path.exists(handle.view)


def _read_plain_file(plain, rel, tmp_path):
    mnt = str(tmp_path / "plainmnt")
    os.makedirs(mnt, exist_ok=True)
    sh("mount", "-t", "ntfs-3g", "-o", "ro,loop", plain, mnt)
    try:
        return open(os.path.join(mnt, rel), "rb").read()
    finally:
        sh("umount", mnt)


@needs_env
def test_wrong_key_is_rejected_with_a_helpful_error(disk):
    other = bli.make_recovery_password(99)
    with pytest.raises(bitlocker.BitLockerError) as exc:
        bitlocker.unlock(disk, other)
    assert exc.value.kind == "wrong_key" and "doesn't unlock" in str(exc.value)


@needs_env
def test_key_never_appears_on_the_command_line(disk):
    handle = bitlocker.unlock(disk, PASSWORD)
    cmdlines = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmdlines.append(open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore"))
        except OSError:
            pass
    assert not any(PASSWORD.replace("-", "")[:12] in c or PASSWORD[:13] in c for c in cmdlines)
    bitlocker.lock(handle.device)


@needs_env
def test_non_bitlocker_partition_is_reported(tmp_path):
    junk = str(tmp_path / "junk.img")
    with open(junk, "wb") as f:
        f.truncate(8 * MiB)
    with pytest.raises(bitlocker.BitLockerError) as exc:
        bitlocker.unlock(junk, PASSWORD)
    assert exc.value.kind == "not_bitlocker"


# ------------------------------------------------------------------ decrypt --

@needs_env
def test_preflight_reports_state_and_boot_files(disk, journal_root):
    pre = bd.preflight(disk, PASSWORD, journal_root)
    assert pre.ok, pre.problems
    assert pre.info.healthy and pre.boot_files >= 4
    assert pre.layout.header_bytes == 8192


@needs_env
def test_preflight_refuses_a_volume_that_is_not_settled(plain, tmp_path, journal_root):
    path = str(tmp_path / "paused.img")
    bli.build(plain, path, PASSWORD, state=5)
    with pytest.raises(bd.DecryptError, match="fully encrypted"):
        bd.preflight(path, PASSWORD, journal_root)


@needs_env
def test_decrypt_in_place_gives_back_the_original_ntfs(disk, plain, journal_root):
    seen = []
    info = bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB,
                               progress=lambda phase, done, total, msg: seen.append(phase))
    assert info.healthy
    assert open(disk, "rb").read() == open(plain, "rb").read()         # byte-identical to the plain volume
    assert b"-FVE-FS-" not in open(disk, "rb").read()                  # no BitLocker signature or keys left
    assert {"prepare", "decrypt", "header", "scrub", "verify", "check", "done"} <= set(seen)
    assert not os.listdir(journal_root)                                # journal cleaned up
    assert bitlocker.probe(disk).healthy                               # ntfsresize reads it as plain NTFS


@needs_env
def test_decrypted_volume_keeps_windows_boot_files(disk, plain, journal_root, tmp_path):
    bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB)
    mnt = str(tmp_path / "after")
    os.makedirs(mnt)
    sh("mount", "-t", "ntfs-3g", "-o", "ro,loop", disk, mnt)
    try:
        after = bd.hash_boot_files(mnt)
    finally:
        sh("umount", mnt)
    assert set(after) >= {"Windows/System32/winload.efi", "Windows/System32/config/SYSTEM"}
    for rel in after:
        assert hashlib.sha256(_read_plain_file(plain, rel, tmp_path)).hexdigest() == after[rel]


class Crash(Exception):
    pass


def crash_at(event, nth, tag=None):
    count = {"n": 0}

    def fault(ev, **info):
        if ev == event and (tag is None or info.get("tag") == tag):
            count["n"] += 1
            if count["n"] == nth:
                raise Crash(f"power lost at {ev}#{nth}")
    return fault


CRASHES = [
    ("chunk_journaled", 3, "body"), ("mid_write", 2, "body"), ("chunk_written", 5, "body"),
    ("state_saved", 4, "body"), ("chunk_journaled", 1, "header"), ("mid_write", 1, "header"),
    ("header_written", 1, "header"), ("scrubbed", 1, "scrub"),
]


@needs_env
@pytest.mark.parametrize("event,nth,tag", CRASHES, ids=[f"{e}-{t}-{n}" for e, n, t in CRASHES])
def test_power_loss_at_any_point_is_recoverable(disk, plain, journal_root, event, nth, tag):
    with pytest.raises(Crash):
        bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB, fault=crash_at(event, nth, tag))
    assert bd.find_pending([journal_root]), "an interrupted run must leave a journal to resume from"
    info = bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB)   # "reboot" and run again
    assert info.healthy
    assert open(disk, "rb").read() == open(plain, "rb").read()
    assert not os.listdir(journal_root)


@needs_env
def test_stop_request_between_chunks_is_resumable(disk, plain, journal_root):
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] == 4

    with pytest.raises(bd.DecryptInterrupted):
        bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB, should_stop=stop)
    bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB)
    assert open(disk, "rb").read() == open(plain, "rb").read()


@needs_env
def test_wrong_key_changes_nothing_on_disk(disk, journal_root):
    before = hashlib.sha256(open(disk, "rb").read()).hexdigest()
    with pytest.raises(bitlocker.BitLockerError):
        bd.decrypt_in_place(disk, bli.make_recovery_password(99), journal_root, chunk_size=1 * MiB)
    assert hashlib.sha256(open(disk, "rb").read()).hexdigest() == before


@needs_env
def test_boot_file_corruption_is_never_reported_as_success(disk, journal_root):
    """If anything Windows needs to boot differs afterwards, the run must fail loudly and keep its journal."""
    def sabotage(ev, **info):
        if ev == "scrubbed":                     # after all data and the header are written
            raise_after = True
            with open(disk, "r+b") as f:         # flip bytes inside the first big file's data
                f.seek(3 * MiB)
                f.write(b"\xff" * 4096)
    with pytest.raises(bd.DecryptError):
        bd.decrypt_in_place(disk, PASSWORD, journal_root, chunk_size=1 * MiB, fault=sabotage)
    assert bd.find_pending([journal_root])


@pytest.fixture(scope="module")
def dirty_plain(plain, tmp_path_factory):
    """The same Windows-like volume, but with NTFS' 'needs a check' flag set (as after Fast Startup)."""
    path = str(tmp_path_factory.mktemp("dirty") / "plain.img")
    shutil.copyfile(plain, path)
    subprocess.run(["ntfsfix", path], capture_output=True)       # sets the flag, schedules chkdsk
    assert "scheduled for check" in subprocess.run(["ntfsresize", "--info", path], capture_output=True,
                                                   text=True).stderr + subprocess.run(
        ["ntfsresize", "--info", path], capture_output=True, text=True).stdout
    return path


@needs_env
def test_check_flag_is_a_warning_for_decrypting_not_a_blocker(dirty_plain, tmp_path, journal_root):
    dev = str(tmp_path / "device.img")
    bli.build(dirty_plain, dev, PASSWORD)
    info = bitlocker.probe(dirty_plain)
    assert not info.healthy and info.kind == "dirty" and info.size > 0   # sizes still known (read-only --force)

    pre = bd.preflight(dev, PASSWORD, journal_root)
    assert pre.ok, pre.problems                                           # not a blocker
    assert any("needing a check" in w for w in pre.warnings)

    result = bd.decrypt_in_place(dev, PASSWORD, journal_root, chunk_size=1 * MiB)
    assert result.kind == "dirty"                                         # the flag is carried over unchanged
    assert open(dev, "rb").read() == open(dirty_plain, "rb").read()       # byte-identical: nothing "fixed" or hidden
    bitlocker.lock_all()
