# SPDX-License-Identifier: MIT
"""Turn BitLocker off from Linux: decrypt a Windows 7+/10/11 volume in place.

The volume is read through dislocker (a read-only decrypted *view*) and the
plaintext is written back over the same sectors of the raw partition, after which
it is an ordinary NTFS volume that can be shrunk and dual-booted.

This rewrites the whole Windows partition, so it is built around not losing data:

* **Recoverable at every step.** For each chunk, the plaintext is first saved to a
  journal on persistent storage (the EFI System Partition) and fsync'ed; only then
  is the raw chunk overwritten, fsync'ed and the progress recorded. A chunk that
  was interrupted half-way is simply rewritten from the journal on resume (the
  operation is idempotent). Until the last step the BitLocker metadata is intact,
  so a resumed run can keep decrypting what is still encrypted.
* **Header last.** The partition's first sectors (the BitLocker header, whose real
  NTFS boot sector lives in a backup area) are replaced only after every other
  sector is done and the restored boot sector looks like NTFS. That commit step is
  what turns the volume from "BitLocker" into "plain NTFS".
* **Verified.** Afterwards everything written is read back and compared with the
  recorded checksums, and the file system is checked with ntfsresize/ntfsfix.

What this cannot guarantee: that Windows is happy afterwards. It is tested here
against synthetic volumes that real dislocker reads, not against a Windows boot.
Turning BitLocker off inside Windows ("manage-bde -off C:") remains the safest route.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import bitlocker
from .bitlocker import BitLockerError

MiB = 1024 * 1024
CHUNK = 8 * MiB
METADATA_REGION = 0x10000
INFORMATION_OFFSET_GUID = bytes([0x3B, 0xD6, 0x67, 0x49, 0x29, 0x2E, 0xD8, 0x4A,
                                 0x83, 0x99, 0xF6, 0xA3, 0x39, 0xE3, 0xD0, 0x01])
STATE_ENCRYPTED = 4
SUPPORTED_METHODS = range(0x8000, 0x8006)
JOURNAL_PREFIX = "lintab-decrypt-"
MIN_JOURNAL_FREE = 3 * CHUNK

# Files Windows needs to boot. Their checksums are recorded through the decrypted view before anything is
# written and compared again on the plain volume afterwards, so "Windows is still bootable" rests on
# evidence, not hope. Missing files are skipped (e.g. when decrypting a data drive).
BOOT_FILES = (
    "Windows/System32/winload.efi",
    "Windows/System32/winresume.efi",
    "Windows/System32/ntoskrnl.exe",
    "Windows/System32/hal.dll",
    "Windows/System32/config/SYSTEM",
    "Windows/System32/config/SOFTWARE",
    "Windows/System32/config/SAM",
    "Windows/System32/config/SECURITY",
    "Windows/System32/config/DEFAULT",
    "Windows/System32/drivers/ntfs.sys",
    "Windows/System32/drivers/disk.sys",
    "Windows/System32/smss.exe",
    "Windows/System32/winlogon.exe",
)

Progress = Callable[[str, int, int, str], None]
Fault = Callable[..., None]


class DecryptError(RuntimeError):
    pass


class DecryptInterrupted(DecryptError):
    """Stopped between chunks; running again resumes where it left off."""


# ------------------------------------------------------------------ layout --

@dataclass
class Layout:
    sector_size: int
    volume_size: int
    header_bytes: int
    method: int
    state: int
    volume_guid: str
    protected: list[tuple[int, int]] = field(default_factory=list)


def read_layout(device: str) -> Layout:
    """Parse the BitLocker header and metadata of ``device`` (read-only)."""
    with open(device, "rb") as dev:
        sector0 = dev.read(512)
        if len(sector0) < 512 or sector0[3:11] != b"-FVE-FS-":
            raise DecryptError("This partition has no BitLocker header (already decrypted?).")
        if sector0[160:176] != INFORMATION_OFFSET_GUID:
            raise DecryptError("This BitLocker volume uses an older or unusual layout that isn't supported "
                               "(Windows 7 and later are).")
        sector_size = struct.unpack_from("<H", sector0, 11)[0]
        offsets = struct.unpack_from("<QQQ", sector0, 176)
        last_error = "no valid metadata block found"
        for offset in offsets:
            dev.seek(offset)
            head = dev.read(0x40)
            if len(head) < 0x40 or head[:8] != b"-FVE-FS-":
                continue
            size16, version, state, next_state = struct.unpack_from("<HHHH", head, 8)
            total = size16 << 4
            if version != 2 or not 0x70 < total <= METADATA_REGION:
                last_error = f"unsupported metadata version {version}"
                continue
            dev.seek(offset)
            block = dev.read(total + 8)
            if len(block) < total + 8 or zlib.crc32(block[:total]) & 0xFFFFFFFF != struct.unpack_from("<I", block, total + 4)[0]:
                last_error = "metadata checksum mismatch"
                continue
            volume_size, _, backup_sectors = struct.unpack_from("<QII", block, 0x10)
            backup_addr = struct.unpack_from("<Q", block, 0x38)[0]
            dataset_size = struct.unpack_from("<I", block, 0x40)[0]
            method = struct.unpack_from("<H", block, 0x40 + 0x24)[0]
            guid = block[0x50:0x60].hex()
            header_bytes = _virtualization_bytes(block, min(total, 0x40 + dataset_size))
            if header_bytes <= 0:
                raise DecryptError("Couldn't find the BitLocker virtualization info; refusing to continue.")
            if state != STATE_ENCRYPTED or next_state != STATE_ENCRYPTED:
                raise DecryptError("This drive is not in a settled, fully encrypted state (it may be encrypting, "
                                   "decrypting or paused). Open Windows and let it finish first.")
            if method not in SUPPORTED_METHODS:
                raise DecryptError(f"Encryption method {method:#x} isn't supported.")
            protected = [(o, METADATA_REGION) for o in offsets] + [(backup_addr, header_bytes)]
            for start, length in protected:
                if start % 4096 or length % 512:
                    raise DecryptError("Unexpected BitLocker region alignment; refusing to continue.")
            return Layout(sector_size, volume_size, header_bytes, method, state, guid, sorted(protected))
        raise DecryptError(f"Couldn't read the BitLocker metadata ({last_error}).")


def _virtualization_bytes(block: bytes, end: int) -> int:
    """Size of the virtualized header area (the VIRTUALIZATION datum: entry 0xf, value 0xf)."""
    pos = 0x70
    while pos + 8 <= end:
        size, entry, value, _ = struct.unpack_from("<HHHH", block, pos)
        if size < 8:
            break
        if entry == 0xF and value == 0xF and pos + 24 <= len(block):
            return struct.unpack_from("<Q", block, pos + 16)[0]
        pos += size
    return 0


def body_segments(layout: Layout, start: int) -> list[tuple[int, int]]:
    """Byte ranges to convert in the body pass: everything after the header that is
    not a BitLocker metadata/backup region, starting at ``start``."""
    segments, cursor, end = [], layout.header_bytes, layout.volume_size
    for p_start, p_len in layout.protected:
        if p_start < layout.header_bytes:
            continue
        if p_start > cursor:
            segments.append((cursor, min(p_start, end)))
        cursor = max(cursor, p_start + p_len)
    if cursor < end:
        segments.append((cursor, end))
    clipped = []
    for a, b in segments:
        a = max(a, start)
        if b > a:
            clipped.append((a, b))
    return clipped


# ----------------------------------------------------------------- journal --

class Journal:
    """Durable progress record. Every update is write-temp, fsync, rename, fsync dir."""

    def __init__(self, directory: str) -> None:
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _fsync_dir(self) -> None:
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write(self, name: str, data: bytes) -> None:
        tmp = self._path(name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path(name))
        self._fsync_dir()

    def load_state(self) -> Optional[dict]:
        try:
            with open(self._path("state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save_state(self, state: dict) -> None:
        self._atomic_write("state.json", json.dumps(state).encode())

    def save_chunk(self, offset: int, data: bytes) -> None:
        """Park the plaintext of a chunk before the raw device is touched."""
        with open(self._path("chunk.bin"), "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self._atomic_write("chunk.json", json.dumps(
            {"offset": offset, "length": len(data), "crc": zlib.crc32(data) & 0xFFFFFFFF}).encode())

    def pending_chunk(self) -> Optional[tuple[int, bytes]]:
        try:
            with open(self._path("chunk.json")) as f:
                meta = json.load(f)
            with open(self._path("chunk.bin"), "rb") as f:
                data = f.read()
        except (OSError, ValueError):
            return None
        if len(data) != meta["length"] or zlib.crc32(data) & 0xFFFFFFFF != meta["crc"]:
            return None  # the journal write itself was cut short; the raw chunk was not touched yet
        return meta["offset"], data

    def clear_chunk(self) -> None:
        for name in ("chunk.json", "chunk.bin"):
            try:
                os.remove(self._path(name))
            except OSError:
                pass
        self._fsync_dir()

    def log_chunk(self, offset: int, length: int, crc: int) -> None:
        with open(self._path("progress.log"), "a") as f:
            f.write(f"{offset} {length} {crc:08x}\n")
            f.flush()
            os.fsync(f.fileno())

    def read_log(self) -> dict[int, tuple[int, int]]:
        entries: dict[int, tuple[int, int]] = {}
        try:
            with open(self._path("progress.log")) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) == 3:
                        entries[int(parts[0])] = (int(parts[1]), int(parts[2], 16))
        except OSError:
            pass
        return entries

    def destroy(self) -> None:
        """Remove the journal; the chunk file held a copy of Windows data, so zero it first."""
        try:
            size = os.path.getsize(self._path("chunk.bin"))
            with open(self._path("chunk.bin"), "r+b") as f:
                f.write(b"\x00" * size)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        for name in os.listdir(self.dir):
            try:
                os.remove(self._path(name))
            except OSError:
                pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass


def journal_dir_for(root: str, volume_guid: str) -> str:
    return os.path.join(root, JOURNAL_PREFIX + volume_guid[:16])


def find_pending(roots: list[str]) -> list[dict]:
    """Interrupted decryptions found under the given directories (e.g. ESP mount points)."""
    found = []
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if name.startswith(JOURNAL_PREFIX):
                state = Journal(os.path.join(root, name)).load_state()
                if state:
                    found.append({"journal": os.path.join(root, name), **state})
    return found


# --------------------------------------------------------------- preflight --

@dataclass
class Preflight:
    layout: Layout
    info: bitlocker.VolumeInfo
    ac_power: bool
    journal_free: int
    problems: list[str]
    warnings: list[str]
    boot_files: int = 0  # how many of the known Windows boot files exist on the volume

    @property
    def ok(self) -> bool:
        return not self.problems


def on_ac_power(sys_class: str = "/sys/class/power_supply") -> bool:
    """True when mains/USB power is online, or there is no battery to worry about."""
    has_battery, online = False, False
    try:
        for name in os.listdir(sys_class):
            base = os.path.join(sys_class, name)
            kind = open(os.path.join(base, "type")).read().strip()
            if kind == "Battery":
                has_battery = True
            elif os.path.exists(os.path.join(base, "online")) and open(os.path.join(base, "online")).read().strip() == "1":
                online = True
    except OSError:
        return True
    return online or not has_battery


def preflight(device: str, recovery_key: str, journal_root: str,
              handle: Optional[bitlocker.Unlocked] = None) -> Preflight:
    """Everything that can be checked without writing: key, state, file system, power, space."""
    problems: list[str] = []
    warnings: list[str] = []
    layout = read_layout(device)  # raises DecryptError for unsupported/unsettled volumes
    own_handle = handle is None
    handle = handle or bitlocker.unlock(device, recovery_key, read_only=True)
    try:
        with open(handle.view, "rb") as view:
            first = view.read(512)
        if first[3:11] != b"NTFS    ":
            problems.append("The unlocked drive doesn't contain an NTFS (Windows) file system.")
        info = bitlocker.probe(handle.view)
        if not info.healthy:
            if info.kind == "dirty":
                # Decrypting copies bytes exactly as they are, so a "needs a check" flag doesn't matter here.
                # It still has to be cleared (chkdsk) before the drive can be shrunk, but with BitLocker off
                # that no longer needs an unlock.
                warnings.append("Windows marked this drive as needing a check. That doesn't affect turning "
                                "BitLocker off. Afterwards run chkdsk in Windows (no unlocking needed any more); "
                                "it has to be done before LintabOS can shrink Windows.")
            else:
                problems.append(info.problem)
        mnt = tempfile.mkdtemp(prefix="lintab-bootcheck-")
        try:
            _mount_ntfs_ro(handle.view, mnt)
            try:
                boot_files = sum(1 for rel in BOOT_FILES if _find_ci(mnt, rel))
            finally:
                subprocess.run(["umount", mnt], capture_output=True)
        except DecryptError:
            boot_files = 0
        finally:
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        if boot_files == 0:
            warnings.append("No Windows system files were found on this drive; it may be a data drive.")
    finally:
        if own_handle:
            bitlocker.lock(device)
    ac = on_ac_power()
    if not ac:
        problems.append("Plug in the charger first. Losing power halfway makes this much riskier.")
    try:
        st = os.statvfs(journal_root)
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = 0
    if free < MIN_JOURNAL_FREE:
        problems.append("There's no safe place to keep the recovery journal (the EFI partition needs about "
                        f"{MIN_JOURNAL_FREE // MiB} MB free).")
    return Preflight(layout, info, ac, free, problems, warnings, boot_files)


# ----------------------------------------------------- Windows boot files --

def _mount_ntfs_ro(source: str, mount_point: str) -> None:
    """Read-only NTFS mount of an image file, decrypted view or raw partition."""
    os.makedirs(mount_point, exist_ok=True)
    is_block = stat.S_ISBLK(os.stat(source).st_mode)
    opts = "ro" if is_block else "ro,loop"
    errors = []
    for fstype in ("ntfs3", "ntfs-3g"):
        proc = subprocess.run(["mount", "-t", fstype, "-o", opts, source, mount_point],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return
        errors.append((proc.stderr or proc.stdout).strip())
    raise DecryptError("Could not read the Windows files to check them: " + "; ".join(e for e in errors if e))


def hash_boot_files(mount_point: str) -> dict[str, str]:
    result = {}
    for rel in BOOT_FILES:
        path = os.path.join(mount_point, rel)
        # NTFS is case-insensitive; the mount is not, so match case-insensitively.
        actual = _find_ci(mount_point, rel)
        if actual is None:
            continue
        digest = hashlib.sha256()
        with open(actual, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        result[rel] = digest.hexdigest()
    return result


def _find_ci(root: str, rel: str) -> Optional[str]:
    cur = root
    for part in rel.split("/"):
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        match = next((n for n in names if n.lower() == part.lower()), None)
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur if os.path.isfile(cur) else None


def manifest_from_view(handle: bitlocker.Unlocked) -> dict[str, str]:
    mnt = tempfile.mkdtemp(prefix="lintab-bootcheck-")
    try:
        _mount_ntfs_ro(handle.view, mnt)
        try:
            return hash_boot_files(mnt)
        finally:
            subprocess.run(["umount", mnt], capture_output=True)
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass


def check_boot_files(device: str, manifest: dict[str, str]) -> None:
    """Re-read the boot files from the now-plain volume and require identical contents."""
    mnt = tempfile.mkdtemp(prefix="lintab-bootcheck-")
    try:
        _mount_ntfs_ro(device, mnt)
        try:
            after = hash_boot_files(mnt)
        finally:
            subprocess.run(["umount", mnt], capture_output=True)
    finally:
        try:
            os.rmdir(mnt)
        except OSError:
            pass
    bad = [rel for rel, digest in manifest.items() if after.get(rel) != digest]
    if bad:
        raise DecryptError("Windows system files changed during decryption: " + ", ".join(bad)
                           + ". Do NOT boot Windows. The recovery journal was kept.")


# ----------------------------------------------------------------- the ESP --

ESP_RUN = "/run/lintab"


def mount_esp(device: str, name: str = "esp") -> str:
    """Mount an EFI System Partition read-write; the recovery journal lives there because it
    survives a power cut and the live USB does not."""
    mnt = os.path.join(ESP_RUN, name)
    os.makedirs(mnt, exist_ok=True)
    if not os.path.ismount(mnt):
        proc = subprocess.run(["mount", "-t", "vfat", "-o", "rw,umask=0077", device, mnt],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise DecryptError("Could not open the EFI partition for the recovery journal: "
                               + (proc.stderr or proc.stdout).strip())
    return mnt


def unmount_esp(mnt: str) -> None:
    subprocess.run(["sync"])
    if os.path.ismount(mnt):
        subprocess.run(["umount", mnt], capture_output=True)
    try:
        os.rmdir(mnt)
    except OSError:
        pass


# ------------------------------------------------------------------ decrypt --

def _noop(*_a, **_k) -> None:
    return None


def decrypt_in_place(device: str, recovery_key: str, journal_root: str, chunk_size: int = CHUNK,
                     progress: Optional[Progress] = None, fault: Optional[Fault] = None,
                     should_stop: Optional[Callable[[], bool]] = None) -> bitlocker.VolumeInfo:
    """Decrypt ``device`` in place; resumes automatically from an existing journal.

    ``fault`` is a test hook called at well-defined points (it may raise to simulate a crash).
    Returns the file system info of the finished, plain NTFS volume.
    """
    progress = progress or _noop
    fault = fault or _noop
    if chunk_size % 4096:
        raise DecryptError("chunk size must be a multiple of 4096")

    existing = find_pending([journal_root])
    state: Optional[dict] = None
    journal: Optional[Journal] = None
    for entry in existing:
        if _same_volume(device, entry):
            journal = Journal(entry["journal"])
            state = journal.load_state()
            break

    if state is None:
        layout = read_layout(device)
        journal = Journal(journal_dir_for(journal_root, layout.volume_guid))
        state = {
            "version": 1, "device": device, "device_size": _device_size(device),
            "volume_guid": layout.volume_guid, "sector_size": layout.sector_size,
            "volume_size": layout.volume_size, "header_bytes": layout.header_bytes,
            "method": layout.method, "protected": layout.protected, "phase": "body",
            "next_offset": layout.header_bytes, "chunk_size": chunk_size, "started": int(time.time()),
        }
        journal.save_state(state)
    assert journal is not None
    layout = Layout(state["sector_size"], state["volume_size"], state["header_bytes"], state["method"],
                    STATE_ENCRYPTED, state["volume_guid"], [tuple(p) for p in state["protected"]])
    chunk_size = state["chunk_size"]

    fd = os.open(device, os.O_RDWR)
    handle: Optional[bitlocker.Unlocked] = None
    try:
        _replay_pending_chunk(journal, state, fd, fault)

        if state["phase"] in ("body", "header"):
            handle = bitlocker.unlock(device, recovery_key, read_only=True)
            if "manifest" not in state:  # before the first write: what Windows needs to boot, as it is now
                progress("prepare", 0, 1, "Recording Windows' boot files")
                state["manifest"] = manifest_from_view(handle)
                journal.save_state(state)
            view_fd = os.open(handle.view, os.O_RDONLY)
            try:
                if state["phase"] == "body":
                    _body_pass(fd, view_fd, journal, state, layout, chunk_size, progress, fault, should_stop)
                _header_commit(fd, view_fd, journal, state, layout, progress, fault)
            finally:
                os.close(view_fd)
            bitlocker.lock(device)
            handle = None

        if state["phase"] == "scrub":
            _scrub(fd, journal, state, layout, progress, fault)
        if state["phase"] == "verify":
            _verify(fd, journal, state, layout, chunk_size, progress)
            state["phase"] = "check"
            journal.save_state(state)
    finally:
        os.close(fd)
        if handle is not None:
            bitlocker.lock(device)

    progress("check", 0, 1, "Checking the Windows file system")
    info = bitlocker.probe(device)
    if not info.healthy and info.kind != "dirty":
        raise DecryptError("The decrypted drive failed its file system check: " + info.problem
                           + "\nThe recovery journal was kept so nothing is lost; do not reboot into Windows yet.")
    progress("check", 0, 1, "Checking Windows' boot files")
    check_boot_files(device, state.get("manifest", {}))
    journal.destroy()
    progress("done", 1, 1, "BitLocker is off")
    return info


def _device_size(device: str) -> int:
    with open(device, "rb") as dev:
        return dev.seek(0, os.SEEK_END)


def _same_volume(device: str, entry: dict) -> bool:
    """Does this journal belong to ``device``? The device path can change between boots,
    so compare size and, while the BitLocker header still exists, its volume GUID."""
    try:
        if _device_size(device) != entry.get("device_size"):
            return False
        if entry.get("phase") == "body":
            return read_layout(device).volume_guid == entry.get("volume_guid")
        # Once the header is being restored it is no longer readable; size is all that is left.
        return True
    except (OSError, DecryptError):
        return False


def _pwrite_all(fd: int, data: bytes, offset: int, fault: Fault, tag: str) -> None:
    half = (len(data) // 2) // 4096 * 4096
    if half:
        os.pwrite(fd, data[:half], offset)
        fault("mid_write", tag=tag, offset=offset)
    view = memoryview(data)[half:]
    pos = offset + half
    while len(view):
        n = os.pwrite(fd, view, pos)
        view, pos = view[n:], pos + n


def _replay_pending_chunk(journal: Journal, state: dict, fd: int, fault: Fault) -> None:
    pending = journal.pending_chunk()
    if not pending:
        journal.clear_chunk()
        return
    offset, data = pending
    _pwrite_all(fd, data, offset, fault, "replay")
    os.fsync(fd)
    if state["phase"] == "body" and offset == state["next_offset"]:
        journal.log_chunk(offset, len(data), zlib.crc32(data) & 0xFFFFFFFF)
        state["next_offset"] = offset + len(data)
        journal.save_state(state)
    elif state["phase"] == "header" and offset == 0:
        journal.log_chunk(0, len(data), zlib.crc32(data) & 0xFFFFFFFF)
        state["phase"] = "scrub"
        journal.save_state(state)
    journal.clear_chunk()


def _write_chunk(fd: int, journal: Journal, offset: int, data: bytes, fault: Fault, tag: str) -> None:
    journal.save_chunk(offset, data)
    fault("chunk_journaled", tag=tag, offset=offset)
    _pwrite_all(fd, data, offset, fault, tag)
    os.fsync(fd)
    fault("chunk_written", tag=tag, offset=offset)


def _body_pass(fd: int, view_fd: int, journal: Journal, state: dict, layout: Layout, chunk_size: int,
               progress: Progress, fault: Fault, should_stop: Optional[Callable[[], bool]]) -> None:
    total = sum(b - a for a, b in body_segments(layout, layout.header_bytes))
    done_before = total - sum(b - a for a, b in body_segments(layout, state["next_offset"]))
    done = done_before
    for seg_start, seg_end in body_segments(layout, state["next_offset"]):
        offset = seg_start
        while offset < seg_end:
            if should_stop and should_stop():
                raise DecryptInterrupted("Stopped; run the installer again to continue.")
            length = min(chunk_size, seg_end - offset)
            data = os.pread(view_fd, length, offset)
            if len(data) != length:
                raise DecryptError(f"Short read from the decrypted view at {offset:#x}.")
            _write_chunk(fd, journal, offset, data, fault, "body")
            journal.log_chunk(offset, length, zlib.crc32(data) & 0xFFFFFFFF)
            state["next_offset"] = offset + length
            journal.save_state(state)
            journal.clear_chunk()
            fault("state_saved", tag="body", offset=offset)
            offset += length
            done += length
            progress("decrypt", done, total, "Decrypting Windows")


def _header_commit(fd: int, view_fd: int, journal: Journal, state: dict, layout: Layout,
                   progress: Progress, fault: Fault) -> None:
    if state["phase"] != "body" and state["phase"] != "header":
        return
    state["phase"] = "header"
    journal.save_state(state)
    progress("header", 0, 1, "Restoring the Windows boot sector")
    data = os.pread(view_fd, layout.header_bytes, 0)
    if len(data) != layout.header_bytes or data[3:11] != b"NTFS    ":
        raise DecryptError("The restored boot sector doesn't look like NTFS; stopping before the commit step. "
                           "Nothing at the start of the partition was changed.")
    _write_chunk(fd, journal, 0, data, fault, "header")
    fault("header_written", tag="header", offset=0)
    journal.log_chunk(0, len(data), zlib.crc32(data) & 0xFFFFFFFF)
    state["phase"] = "scrub"
    journal.save_state(state)
    journal.clear_chunk()


def _scrub(fd: int, journal: Journal, state: dict, layout: Layout, progress: Progress, fault: Fault) -> None:
    """Zero the leftover BitLocker metadata and backup regions (they hold key material)."""
    progress("scrub", 0, 1, "Removing leftover BitLocker data")
    for start, length in layout.protected:
        if start >= layout.header_bytes:
            os.pwrite(fd, b"\x00" * length, start)
    os.fsync(fd)
    fault("scrubbed", tag="scrub", offset=0)
    state["phase"] = "verify"
    journal.save_state(state)


def _verify(fd: int, journal: Journal, state: dict, layout: Layout, chunk_size: int, progress: Progress) -> None:
    entries = journal.read_log()
    total = sum(length for length, _ in entries.values()) or 1
    done = 0
    for offset in sorted(entries):
        length, crc = entries[offset]
        data = os.pread(fd, length, offset)
        if len(data) != length or zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise DecryptError(f"Verification failed at {offset:#x}: what was written does not match. "
                               "The recovery journal was kept; do not reboot into Windows.")
        done += length
        progress("verify", done, total, "Verifying")
    for start, length in layout.protected:
        if start >= layout.header_bytes and any(os.pread(fd, length, start)):
            raise DecryptError("Leftover BitLocker metadata was not erased.")
