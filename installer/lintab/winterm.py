# SPDX-License-Identifier: MIT
"""WinTermMod: a terminal, positioned at your mounted Windows drive.

Mounts the Windows partition read-write (same Fast-Startup/BitLocker safety checks as LinWinMod's Files tab) and
opens a normal Linux terminal there — so ``find``, ``grep``, bulk renames and other command-line tools can run
directly against your Windows files, which a GUI file manager alone doesn't offer.

This is **not** a real Windows ``cmd.exe``, and deliberately does not try to be one. A native Windows program cannot
run outside Windows at all — there is no Windows kernel, registry or services underneath to give it anything to work
with — and running one against your actual Windows system drive through a compatibility layer (Wine, rather than a
disposable Wine prefix) is not something LintabOS does: in effect, it would be a way to run arbitrary Windows
administrative tools against your system without logging into Windows, carrying the same risk as the registry write
access LinWinMod's own registry browser deliberately does not have (see docs/LEGAL.md). What you get instead is a
real, ordinary Linux shell, which is exactly what a terminal on this tablet always is — just started in the right
place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

from . import bitlocker, winfiles, winmod

TERMINALS = ("gnome-terminal", "ptyxis", "x-terminal-emulator", "xterm")


def find_terminal(which=shutil.which) -> Optional[str]:
    for name in TERMINALS:
        found = which(name)
        if found:
            return found
    return None


def terminal_command(terminal: str, directory: str) -> list[str]:
    if os.path.basename(terminal) == "xterm":
        return [terminal, "-e", f"cd {directory!r} && exec $SHELL"]
    return [terminal, "--working-directory", directory]   # gnome-terminal, ptyxis, and most x-terminal-emulator targets


def tell(message: str, kind: str = "--info", run=subprocess.run) -> None:
    zenity = shutil.which("zenity")   # looked up here, not as a default argument, so tests can monkeypatch shutil.which
    if zenity:
        run([zenity, kind, "--title=WinTermMod", "--width=440", f"--text={message}"])
    else:
        print(message, file=sys.stderr)


def open_terminal(run=subprocess.run, popen=subprocess.Popen) -> int:
    """Never raises: every failure is reported through a message instead. Returns a process-style exit code."""
    partitions = []
    try:
        partitions = winfiles.windows_partitions()
    except Exception as exc:  # noqa: BLE001 - must still report, not crash with no window to show it in
        tell(f"Could not look at the disks: {exc}", "--error", run)
        return 1
    if not partitions:
        tell("No Windows partition was found on this tablet. Open LinWinMod's Files tab first — it can also pick "
            "a partition by hand if automatic detection doesn't find yours.", "--error", run)
        return 1
    partition = max(partitions, key=lambda p: p.size)

    try:
        info = bitlocker.probe(partition.path)
    except Exception as exc:  # noqa: BLE001
        tell(f"Could not check the drive: {exc}", "--error", run)
        return 1
    if not info.healthy:
        messages = {
            "dirty": "Windows flagged this drive for a consistency check. Boot Windows, let it check the disk, "
                    "then shut down fully and try again.",
            "hibernated": "Windows is hibernated (Fast Startup). Boot Windows, run “powercfg /h off”, "
                         "shut down fully, then try again.",
        }
        tell(messages.get(info.problem, info.problem or "This drive isn't safe to use right now."), "--error", run)
        return 1

    terminal = find_terminal()
    if not terminal:
        tell("No terminal application is installed.", "--error", run)
        return 1

    proc = winmod.ensure_writable(run=run)
    if getattr(proc, "returncode", 1) != 0:
        message = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        tell(message or "That didn't work (cancelled, or not allowed).", "--error", run)
        return 1

    popen(terminal_command(terminal, winmod.MOUNT_POINT))
    return 0


def main() -> int:
    return open_terminal()


if __name__ == "__main__":
    sys.exit(main())
