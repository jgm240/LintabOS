# SPDX-License-Identifier: MIT
"""LintabOS Extras: what gets installed for each choice, how failures are handled, and that nothing runs unasked."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import extras  # noqa: E402


def test_choices_are_validated_and_deduplicated():
    assert [e.key for e in extras.by_key(["windows", "drawing", "windows"])] == ["windows", "drawing"]
    with pytest.raises(ValueError, match="nope"):
        extras.by_key(["drawing", "nope"])


def test_flatpak_extras_install_for_this_user_only_without_a_password():
    steps = extras.plan(["drawing", "windows"])
    assert steps[0][1][:5] == ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub"]
    installs = [argv for _d, argv in steps[1:]]
    assert all(a[:3] == ["flatpak", "install", "--user"] and "--noninteractive" in a for a in installs)
    assert "com.github.flxzt.rnote" in installs[0] and "com.github.xournalpp.xournalpp" in installs[0]
    assert "com.usebottles.bottles" in installs[1]
    assert not any("pkexec" in a or "sudo" in a for _d, a in steps)


def test_waydroid_goes_through_the_polkit_helper_and_adds_no_flathub_step():
    steps = extras.plan(["android"])
    assert steps == [("Installing Android apps (Waydroid)", ["pkexec", extras.WAYDROID_HELPER])]


def test_one_failure_does_not_stop_the_others():
    def run(argv):
        return 1 if "com.usebottles.bottles" in argv else 0
    assert extras.install(["drawing", "windows", "office"], run=run, log=lambda m: None) == {
        "drawing": True, "windows": False, "office": True}


def test_if_flathub_cannot_be_added_no_flatpak_extra_is_attempted_but_android_still_is():
    calls = []

    def run(argv):
        calls.append(argv)
        return 1 if argv[:2] == ["flatpak", "remote-add"] else 0
    results = extras.install(["drawing", "android"], run=run, log=lambda m: None)
    assert results == {"drawing": False, "android": True}
    assert not any(a[:2] == ["flatpak", "install"] for a in calls)


def test_installed_detection_uses_the_right_probe():
    seen = []
    drawing, android = extras.by_key(["drawing", "android"])
    assert extras.is_installed(drawing, run=lambda a: seen.append(a) or 0) is True
    assert seen == [["flatpak", "info", "--user", "com.github.flxzt.rnote"]]
    assert extras.is_installed(drawing, run=lambda a: 1) is False


def test_cli_install_reports_and_rejects_unknown_names(monkeypatch, capsys):
    monkeypatch.setattr(extras, "_run", lambda argv: 0)
    assert extras.main(["install", "drawing"]) == 0 and "drawing: installed" in capsys.readouterr().out
    assert extras.main(["install", "bogus"]) == 2
    monkeypatch.setattr(extras, "_run", lambda argv: 1)
    assert extras.main(["install", "drawing"]) == 1 and "FAILED" in capsys.readouterr().out


def test_the_waydroid_helper_is_valid_shell_and_does_what_it_says():
    helper = os.path.join(os.path.dirname(__file__), "..", "payload/usr/libexec/lintab/install-waydroid")
    assert subprocess.run(["sh", "-n", helper]).returncode == 0
    text = open(helper).read()
    assert "-t trixie-backports waydroid" in text and "binder_linux" in text and "waydroid init" in text
    assert "set -eu" in text and 'id -u' in text                       # refuses to run unprivileged, stops on errors
    policy = open(os.path.join(os.path.dirname(helper), "../../share/polkit-1/actions/org.lintabos.extras.policy")).read()
    assert "<allow_active>auth_admin</allow_active>" in policy and "/usr/libexec/lintab/install-waydroid" in policy
