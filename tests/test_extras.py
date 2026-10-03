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
    assert "microsoft" in [e.key for e in extras.CATALOG]
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


# ------------------------------------------------- Microsoft apps: optional, per user --

ROOT = os.path.join(os.path.dirname(__file__), "..")
MS_FILES = ("lintab-word", "lintab-excel", "lintab-powerpoint", "lintab-onedrive", "lintab-teams", "lintab-connect-onedrive")


def test_the_microsoft_apps_are_no_longer_part_of_the_base_system():
    applications = os.listdir(os.path.join(ROOT, "payload/usr/share/applications"))
    for name in MS_FILES:
        assert f"{name}.desktop" not in applications                                   # not in every user's app grid by default
        assert os.path.isfile(os.path.join(ROOT, "payload/usr/share/lintabos/extras/microsoft", f"{name}.desktop"))
    ms = extras.by_key(["microsoft"])[0]
    assert ms.kind == "launchers" and "Microsoft account" in ms.description


def test_the_shortcuts_are_added_to_and_removed_from_the_users_own_folder_only(tmp_path):
    src, dest, profile = tmp_path / "src", tmp_path / "home/applications", tmp_path / "home/webapps"
    src.mkdir()
    for name in ("lintab-word", "lintab-teams"):
        (src / f"{name}.desktop").write_text("[Desktop Entry]\nName=x\n")
    dest.mkdir(parents=True)
    (dest / "my-own-app.desktop").write_text("[Desktop Entry]\nName=mine\n")
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("signed in")
    assert extras.add_launchers("microsoft", str(src), str(dest)) == ["lintab-teams.desktop", "lintab-word.desktop"]
    assert (dest / "lintab-word.desktop").exists()
    removed = extras.remove_launchers("microsoft", str(src), str(dest), str(profile))
    assert removed == ["lintab-teams.desktop", "lintab-word.desktop"]
    assert (dest / "my-own-app.desktop").exists()                                      # never touches anything else
    assert not profile.exists()                                                        # the Microsoft sign-in is forgotten


def test_keeping_the_profile_is_possible(tmp_path):
    src, dest, profile = tmp_path / "s", tmp_path / "d", tmp_path / "p"
    for d in (src, dest, profile):
        d.mkdir()
    (src / "lintab-word.desktop").write_text("x")
    (dest / "lintab-word.desktop").write_text("x")
    extras.remove_launchers("microsoft", str(src), str(dest), str(profile), delete_profile=False)
    assert profile.exists() and not (dest / "lintab-word.desktop").exists()


def test_chromium_is_installed_only_when_missing_and_its_failure_is_not_fatal():
    with_chromium = extras.plan(["microsoft"], have_chromium=True)
    assert with_chromium == [("Adding Microsoft 365 web apps", ["lintab-extras", "add-launchers", "microsoft"])]
    without = extras.plan(["microsoft"], have_chromium=False)
    assert without[0][1] == ["pkexec", extras.CHROMIUM_HELPER] and without[1][1][:2] == ["lintab-extras", "add-launchers"]

    def run(argv):
        return 1 if argv[0] == "pkexec" else 0                                         # the password prompt was cancelled
    assert extras.install(["microsoft"], run=run, log=lambda m: None, have_chromium=False) == {"microsoft": True}
    assert extras.install(["microsoft"], run=lambda a: 1, log=lambda m: None, have_chromium=True) == {"microsoft": False}


def test_a_failed_android_install_is_reported_as_failed():
    assert extras.install(["android"], run=lambda a: 1, log=lambda m: None) == {"android": False}
    assert extras.install(["android"], run=lambda a: 0, log=lambda m: None) == {"android": True}


# ------------------------------------------------------------------- uninstalling --

def test_removing_flatpak_extras_keeps_their_data_unless_asked():
    keep = extras.plan_remove(["drawing"])
    assert keep == [("Removing Drawing and notes", ["flatpak", "uninstall", "--user", "-y", "--noninteractive",
                                                    "com.github.flxzt.rnote", "com.github.xournalpp.xournalpp"])]
    assert "--delete-data" in extras.plan_remove(["windows"], delete_data=True)[0][1]
    assert not any("pkexec" in a or "sudo" in a for _d, a in extras.plan_remove(["drawing", "office", "windows", "microsoft"]))


def test_removing_android_leaves_android_mode_first_and_needs_the_polkit_helper():
    steps = extras.plan_remove(["android"])
    assert [a for _d, a in steps] == [["lintab-android", "back"], ["pkexec", extras.WAYDROID_REMOVE_HELPER]]
    assert "DELETES" in extras.by_key(["android"])[0].removal_note                       # said plainly before confirming
    results = extras.remove(["android"], run=lambda a: 1 if a[0] == "lintab-android" else 0, log=lambda m: None)
    assert results == {"android": True}                                                  # leaving Android mode is best effort
    assert extras.remove(["android"], run=lambda a: 1 if a[0] == "pkexec" else 0, log=lambda m: None) == {"android": False}


def test_every_extra_says_what_removing_it_does():
    for extra in extras.CATALOG:
        assert extra.removal_note


def test_the_removal_cli_and_the_helpers(monkeypatch, capsys):
    monkeypatch.setattr(extras, "_run", lambda argv: 0)
    assert extras.main(["remove", "drawing"]) == 0 and "drawing: removed" in capsys.readouterr().out
    assert extras.main(["remove", "bogus"]) == 2
    for helper in ("install-chromium", "remove-waydroid"):
        path = os.path.join(ROOT, "payload/usr/libexec/lintab", helper)
        assert subprocess.run(["sh", "-n", path]).returncode == 0
        assert "id -u" in open(path).read() and "set -eu" in open(path).read()
    remove_text = open(os.path.join(ROOT, "payload/usr/libexec/lintab/remove-waydroid")).read()
    assert "rm -rf /var/lib/waydroid" in remove_text and "PKEXEC_UID" in remove_text    # only the invoking user's own folder
    policy = open(os.path.join(ROOT, "payload/usr/share/polkit-1/actions/org.lintabos.extras.policy")).read()
    assert "org.lintabos.extras-chromium" in policy and "org.lintabos.extras-remove-waydroid" in policy
