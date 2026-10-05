# SPDX-License-Identifier: MIT
"""WinTermMod: a terminal at the mounted Windows drive, not Windows cmd.exe. Terminal/argv selection, and every
failure path (no partition, unclean drive, mount refused) reports a message instead of silently doing nothing."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import winfiles, winterm  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


# ------------------------------------------------------------------ terminal choice --

def test_the_first_available_terminal_from_the_preferred_list_is_used():
    have = {"ptyxis": "/usr/bin/ptyxis", "xterm": "/usr/bin/xterm"}
    assert winterm.find_terminal(which=have.get) == "/usr/bin/ptyxis"     # gnome-terminal not installed, ptyxis is
    assert winterm.find_terminal(which=lambda n: None) is None


def test_gnome_terminal_and_ptyxis_use_working_directory_xterm_uses_a_shell_cd():
    assert winterm.terminal_command("/usr/bin/gnome-terminal", "/media/windows") == \
        ["/usr/bin/gnome-terminal", "--working-directory", "/media/windows"]
    assert winterm.terminal_command("/usr/bin/ptyxis", "/media/windows")[1:] == ["--working-directory", "/media/windows"]
    xterm_cmd = winterm.terminal_command("/usr/bin/xterm", "/media/windows")
    assert xterm_cmd[0] == "/usr/bin/xterm" and xterm_cmd[1] == "-e" and "/media/windows" in xterm_cmd[2]


# -------------------------------------------------------------------------- the flow --

class Recorder:
    def __init__(self):
        self.messages = []
        self.popened = []
        self.which = lambda name: "/usr/bin/zenity" if name == "zenity" else None

    def run(self, argv, **kwargs):
        import subprocess
        if argv and argv[0] == "/usr/bin/zenity":
            self.messages.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def popen(self, argv, **kwargs):
        self.popened.append(argv)


def test_no_windows_partition_reports_a_message_and_opens_nothing(monkeypatch):
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [])
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 1
    assert not rec.popened and rec.messages and "No Windows partition" in rec.messages[0][-1]


def test_disk_discovery_failure_is_reported_not_raised(monkeypatch):
    def boom():
        raise RuntimeError("lsblk exploded")
    monkeypatch.setattr(winfiles, "windows_partitions", boom)
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 1
    assert "lsblk exploded" in rec.messages[0][-1]


def test_an_unclean_drive_is_refused_with_the_right_message(monkeypatch):
    from lintab import bitlocker
    part = winfiles.WindowsPartition("/dev/sda3", "U", 10, "ntfs")
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [part])
    monkeypatch.setattr(bitlocker, "probe",
                        lambda path: type("I", (), {"healthy": False, "problem": "hibernated", "kind": "hibernated"})())
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 1
    assert "Fast Startup" in rec.messages[0][-1] and not rec.popened


def test_an_unreadable_probe_does_not_block_proceeding_to_the_real_privileged_check(monkeypatch):
    """info.kind == "unreadable" means the unprivileged probe simply couldn't tell (no permission to read the
    raw device - confirmed live on a real Duet 3), not that the volume is actually unhealthy. That must not
    refuse outright; it must fall through to the real, privileged mount attempt, same as a healthy result."""
    from lintab import bitlocker, winmod
    part = winfiles.WindowsPartition("/dev/sda3", "U", 10, "ntfs")
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [part])
    monkeypatch.setattr(bitlocker, "probe",
                        lambda path: type("I", (), {"healthy": False, "problem": "unreadable", "kind": "unreadable"})())
    monkeypatch.setattr(winterm, "find_terminal", lambda **k: "/usr/bin/gnome-terminal")
    import subprocess
    monkeypatch.setattr(winmod, "ensure_writable", lambda run=None: subprocess.CompletedProcess([], 0, "", ""))
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 0
    assert rec.popened                                      # reached the terminal, not refused on "unreadable"


def test_a_refused_mount_is_reported_and_no_terminal_opens(monkeypatch):
    from lintab import bitlocker, winmod
    part = winfiles.WindowsPartition("/dev/sda3", "U", 10, "ntfs")
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [part])
    monkeypatch.setattr(bitlocker, "probe", lambda path: type("I", (), {"healthy": True})())
    monkeypatch.setattr(winterm, "find_terminal", lambda **k: "/usr/bin/gnome-terminal")
    import subprocess
    monkeypatch.setattr(winmod, "ensure_writable", lambda run=None: subprocess.CompletedProcess([], 1, "", "nope"))
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 1
    assert not rec.popened and "nope" in rec.messages[0][-1]


def test_success_mounts_then_opens_a_terminal_at_the_mount_point(monkeypatch):
    from lintab import bitlocker, winmod
    part = winfiles.WindowsPartition("/dev/sda3", "U", 10, "ntfs")
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [part])
    monkeypatch.setattr(bitlocker, "probe", lambda path: type("I", (), {"healthy": True})())
    monkeypatch.setattr(winterm, "find_terminal", lambda **k: "/usr/bin/gnome-terminal")
    import subprocess
    monkeypatch.setattr(winmod, "ensure_writable", lambda run=None: subprocess.CompletedProcess([], 0, "", ""))
    rec = Recorder()
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 0
    assert rec.popened == [["/usr/bin/gnome-terminal", "--working-directory", winmod.MOUNT_POINT]]


def test_no_terminal_installed_is_reported_before_anything_is_mounted(monkeypatch):
    from lintab import bitlocker, winmod
    part = winfiles.WindowsPartition("/dev/sda3", "U", 10, "ntfs")
    monkeypatch.setattr(winfiles, "windows_partitions", lambda: [part])
    monkeypatch.setattr(bitlocker, "probe", lambda path: type("I", (), {"healthy": True})())
    monkeypatch.setattr(winterm, "find_terminal", lambda **k: None)
    calls = []
    monkeypatch.setattr(winmod, "ensure_writable", lambda run=None: calls.append(1))
    rec = Recorder()
    monkeypatch.setattr(winterm.shutil, "which", rec.which)
    assert winterm.open_terminal(run=rec.run, popen=rec.popen) == 1
    assert calls == [] and "No terminal" in rec.messages[0][-1]       # never asks for a password for a mount it can't use


# ------------------------------------------------------------------------- honesty --

def test_the_module_says_plainly_it_is_not_real_windows_cmd():
    source = open(os.path.join(ROOT, "installer/lintab/winterm.py")).read()
    assert "not" in source.lower() and "cmd.exe" in source
    desktop = open(os.path.join(ROOT, "payload/usr/share/applications/lintab-winterm.desktop")).read()
    assert "not Windows cmd.exe" in desktop
