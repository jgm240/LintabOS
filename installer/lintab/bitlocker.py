# SPDX-License-Identifier: MIT
"""BitLocker access for LintabOS, built on dislocker.

Everything that touches a BitLocker volume goes through here:

* validating a recovery key the way dislocker does, so typos get a clear message;
* unlocking a volume read-only through ``dislocker-fuse`` (the recovery key is fed
  through a pseudo-terminal, never on the command line where ``ps`` could see it);
* mounting the decrypted view so Windows' files can be browsed or backed up;
* locking again, and probing the decrypted view (size, health).

Decrypting a volume in place lives in :mod:`lintab.bitlocker_decrypt`.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import termios
import time
from dataclasses import dataclass
from typing import Optional

RUN_ROOT = "/run/lintab/bitlocker"
VIEW_NAME = "dislocker-file"


class BitLockerError(RuntimeError):
    """A BitLocker operation failed. ``kind`` lets the UI react to specific cases."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


# ------------------------------------------------------------- recovery key --

def normalize_recovery_key(text: str) -> str:
    """Return the key as ``dddddd-dddddd-...`` (8 blocks) or raise BitLockerError.

    A Windows recovery password is 48 digits in 8 blocks. Each block is a multiple
    of 11 below 65536 * 11 and its sixth digit is a check digit
    ``(d0 - d1 + d2 - d3 + d4) mod 11`` (the same rules dislocker enforces).
    """
    digits = re.sub(r"[\s-]", "", text or "")
    if not digits:
        raise BitLockerError("Enter the 48-digit recovery key.", "key_format")
    if not digits.isdigit():
        raise BitLockerError("The recovery key only contains digits (and dashes).", "key_format")
    if len(digits) != 48:
        raise BitLockerError(f"The recovery key has 48 digits; you entered {len(digits)}.", "key_format")
    blocks = [digits[i:i + 6] for i in range(0, 48, 6)]
    for number, block in enumerate(blocks, 1):
        value = int(block)
        d = [int(c) for c in block]
        if value % 11 != 0 or value >= 720896:
            raise BitLockerError(f"Group {number} ({block}) isn't valid: check that you typed it correctly.",
                                 "key_format")
        if (d[0] - d[1] + d[2] - d[3] + d[4]) % 11 != d[5]:
            raise BitLockerError(f"Group {number} ({block}) has a typo (its check digit doesn't match).",
                                 "key_format")
    return "-".join(blocks)


# ------------------------------------------------------------------ tooling --

def available() -> bool:
    return bool(shutil.which("dislocker-fuse") and (shutil.which("fusermount3") or shutil.which("fusermount")))


def _require_tools() -> None:
    if not available():
        raise BitLockerError("dislocker is not installed, so BitLocker volumes can't be opened here.", "no_tool")
    if not os.path.exists("/dev/fuse"):
        subprocess.run(["modprobe", "fuse"], capture_output=True)
    if not os.path.exists("/dev/fuse"):
        raise BitLockerError("FUSE isn't available in this kernel.", "no_tool")


def _fusermount() -> str:
    return shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount3"


def _name_for(device: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(device.rstrip("/"))) or "volume"


# ------------------------------------------------------------------- unlock --

@dataclass
class Unlocked:
    device: str
    name: str
    run_dir: str
    read_only: bool

    @property
    def view(self) -> str:
        """Path of the decrypted NTFS image exposed by dislocker."""
        return os.path.join(self.run_dir, VIEW_NAME)


_ERROR_MAP = (
    ("None of the provided decryption mean is decrypting", "wrong_key",
     "That recovery key doesn't unlock this drive. Check it against the one in your Microsoft account "
     "(account.microsoft.com/devices/recoverykey) or the printout you saved."),
    ("Cannot parse volume header", "not_bitlocker", "This partition doesn't look like a BitLocker volume."),
    ("signature of the volume", "not_bitlocker", "This partition doesn't look like a BitLocker volume."),
    ("Unable to find a valid dataset", "damaged", "The BitLocker information on this drive is damaged."),
    ("Unsupported BitLocker version", "unsupported", "This BitLocker version isn't supported."),
    ("Algo not supported", "unsupported", "This drive uses an encryption method dislocker can't read."),
    ("not safe", "state", "Windows left this drive in a state that isn't safe to open (it may be mid-encryption "
                         "or hibernated). Boot Windows, shut it down fully and try again."),
)


def _translate(output: str) -> tuple[str, str]:
    for needle, kind, message in _ERROR_MAP:
        if needle in output:
            return kind, message
    tail = " ".join(line.strip() for line in output.strip().splitlines()[-3:])
    return "error", f"dislocker could not open the drive: {tail or 'no details'}"


def _feed_secret_and_wait(argv: list[str], secret: str, timeout: float) -> tuple[int, str]:
    """Run argv with a pseudo-terminal as its *controlling* terminal, answer its secret prompt, and
    return (rc, output).

    dislocker's prompt reads from /dev/tty rather than stdin, hence ``setsid -c`` (make the pty the
    controlling terminal). It also puts the terminal in raw mode, so the secret is only typed once the
    prompt has appeared. The secret never touches argv or the environment.
    """
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)  # never echo the key into the captured output
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = subprocess.Popen(["setsid", "-c", *argv], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    output = b""
    sent = False
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            if not sent and b"recovery password" in output.lower():
                os.write(master, secret.encode())
                sent = True
            if proc.poll() is not None and not ready:
                break
        try:
            rc = proc.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise BitLockerError("Unlocking took too long and was stopped.", "timeout")
        while select.select([master], [], [], 0.1)[0]:  # whatever the daemonized child still prints
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        return rc, output.decode(errors="replace")
    finally:
        os.close(master)


def unlock(device: str, recovery_key: str, read_only: bool = True, run_root: str = RUN_ROOT,
           timeout: float = 180.0) -> Unlocked:
    """Unlock ``device`` with its recovery key; returns a handle to the decrypted view.

    Key stretching is deliberately slow (2**20 SHA-256 rounds), so expect a few
    seconds on a fast machine and more on a tablet.
    """
    _require_tools()
    key = normalize_recovery_key(recovery_key)
    name = _name_for(device)
    run_dir = os.path.join(run_root, name)
    if os.path.ismount(run_dir):
        lock(device, run_root=run_root)
    os.makedirs(run_dir, exist_ok=True)

    argv = ["dislocker-fuse", "-V", device, "-p"]
    if read_only:
        argv.append("-r")
    argv += ["--", run_dir]
    rc, output = _feed_secret_and_wait(argv, key, timeout)
    handle = Unlocked(device=device, name=name, run_dir=run_dir, read_only=read_only)

    # dislocker-fuse daemonizes once mounted; success is the view file appearing.
    for _ in range(50):
        if os.path.exists(handle.view):
            return handle
        time.sleep(0.1)
    kind, message = _translate(output)
    try:
        os.rmdir(run_dir)
    except OSError:
        pass
    raise BitLockerError(message, kind)


def lock(device_or_name: str, run_root: str = RUN_ROOT) -> None:
    """Unmount and close a previously unlocked volume. Safe to call twice."""
    name = _name_for(device_or_name)
    run_dir = os.path.join(run_root, name)
    mount_dir = os.path.join(run_dir, "mnt")
    if os.path.ismount(mount_dir):
        subprocess.run(["umount", mount_dir], capture_output=True)
        if os.path.ismount(mount_dir):
            subprocess.run(["umount", "-l", mount_dir], capture_output=True)
    if os.path.ismount(run_dir):
        subprocess.run([_fusermount(), "-u", run_dir], capture_output=True)
        if os.path.ismount(run_dir):
            subprocess.run([_fusermount(), "-uz", run_dir], capture_output=True)
    for path in (mount_dir, run_dir):
        try:
            os.rmdir(path)
        except OSError:
            pass


def lock_all(run_root: str = RUN_ROOT) -> None:
    if os.path.isdir(run_root):
        for name in os.listdir(run_root):
            lock(name, run_root=run_root)


# -------------------------------------------------------------------- mount --

def mount_view(handle: Unlocked, mount_point: Optional[str] = None, read_only: Optional[bool] = None,
               uid: Optional[int] = None, gid: Optional[int] = None) -> str:
    """Mount the decrypted NTFS view so its files can be read. Returns the mount point.

    Read-only unless the volume was unlocked read-write *and* read_only=False. Mounting
    a Windows volume read-write is refused if it is hibernated (Fast Startup).
    """
    if read_only is None:
        read_only = True
    if not read_only and handle.read_only:
        raise BitLockerError("The drive was unlocked read-only.", "read_only")
    mount_point = mount_point or os.path.join(handle.run_dir, "mnt")
    os.makedirs(mount_point, exist_ok=True)
    if os.path.ismount(mount_point):
        return mount_point
    opts = "loop," + ("ro" if read_only else "rw")
    if uid is not None:  # make the files belong to the person who asked, not root
        opts += f",uid={uid},gid={gid if gid is not None else uid}"
    errors = []
    for fstype in ("ntfs3", "ntfs-3g"):
        proc = subprocess.run(["mount", "-t", fstype, "-o", opts, handle.view, mount_point],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return mount_point
        errors.append((proc.stderr or proc.stdout).strip())
    raise BitLockerError("Could not open the Windows files: " + "; ".join(e for e in errors if e), "mount")


# --------------------------------------------------------------------- info --

@dataclass
class VolumeInfo:
    size: int  # bytes of the NTFS filesystem
    used: int  # bytes in use
    min_size: int  # smallest size it could be shrunk to
    healthy: bool
    problem: str = ""  # user-facing reason when not healthy
    # Why it isn't healthy: "dirty" = flagged for a check (harmless for decrypting, blocks shrinking),
    # "hibernated" = Windows is set to resume (blocks both), "other" = unreadable. "" when healthy.
    kind: str = ""


_SIZE = re.compile(r"Current volume size:\s*(\d+) bytes")
_USED = re.compile(r"Space in use\s*:\s*(\d+) MB")
_MIN = re.compile(r"You might resize at (\d+) bytes")


def probe(path: str) -> VolumeInfo:
    """Inspect an NTFS image or partition (typically the decrypted view) read-only."""
    proc = subprocess.run(["ntfsresize", "--info", "--no-progress-bar", path], capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    low = out.lower()
    size = _SIZE.search(out)
    used = _USED.search(out)
    minimum = _MIN.search(out)
    if proc.returncode == 0 and size and minimum:
        return VolumeInfo(int(size.group(1)), int(used.group(1)) * 1_000_000 if used else 0,
                          int(minimum.group(1)), True)
    if "hibernat" in low or "fast restart" in low or "fast startup" in low:
        return VolumeInfo(0, 0, 0, False,
                          "Windows is hibernated (Fast Startup). Boot Windows, run `powercfg /h off` as "
                          "administrator and shut down fully.", "hibernated")
    if "scheduled for check" in low or "chkdsk" in low or "inconsisten" in low or "dirty" in low:
        # --info with --force only reads; it is used here to still learn the sizes.
        forced = subprocess.run(["ntfsresize", "--info", "--force", "--no-progress-bar", path],
                                capture_output=True, text=True)
        fout = (forced.stdout or "") + (forced.stderr or "")
        fsize, fused, fmin = _SIZE.search(fout), _USED.search(fout), _MIN.search(fout)
        problem = (
            "Windows marked this drive as needing a check (it wasn't shut down cleanly, often after Fast Startup). "
            "This is only a flag, and BitLocker doesn't stop you clearing it:\n"
            "• If Windows starts: choose Restart (not Shut down), sign in, run `chkdsk C: /f` in an administrator "
            "Command Prompt (answer Y), restart again.\n"
            "• If you only get the recovery screen: Troubleshoot → Command Prompt, then "
            "`manage-bde -unlock C: -rp <your 48-digit key>` followed by `chkdsk C: /f` "
            "(find the right letter with `diskpart` → `list volume`).\n"
            "Then run `powercfg /h off` and shut down while holding Shift.")
        return VolumeInfo(int(fsize.group(1)) if fsize else 0,
                          int(fused.group(1)) * 1_000_000 if fused else 0,
                          int(fmin.group(1)) if fmin else 0, False, problem, "dirty")
    problem = "The Windows file system could not be read: " + (out.strip().splitlines() or ["unknown error"])[-1]
    return VolumeInfo(int(size.group(1)) if size else 0, 0, 0, False, problem, "other")
