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
