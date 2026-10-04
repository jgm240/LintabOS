# SPDX-License-Identifier: MIT
"""Restart into Windows / Windows Recovery / the way to Safe Mode: the OsIndications read/write logic, and that a
failed grub-reboot can never still trigger an actual reboot."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import winrecovery  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


# --------------------------------------------------------------- OsIndications --

def test_a_missing_or_short_efivar_file_reads_as_nothing_set(tmp_path):
    assert winrecovery.read_os_indications(str(tmp_path)) == (None, 0)
    path = tmp_path / winrecovery.OS_INDICATIONS_VAR
    path.write_bytes(b"\x07\x00\x00")      # shorter than the 4 (attrs) + 8 (value) minimum
    assert winrecovery.read_os_indications(str(tmp_path)) == (None, 0)


def test_setting_the_bit_creates_the_variable_with_the_standard_attributes(tmp_path):
    assert winrecovery.set_os_indications(winrecovery.START_OS_RECOVERY, str(tmp_path))
    attrs, value = winrecovery.read_os_indications(str(tmp_path))
    assert attrs == winrecovery.DEFAULT_ATTRS and value == winrecovery.START_OS_RECOVERY


def test_setting_the_bit_ors_into_whatever_was_already_set_without_losing_it(tmp_path):
    path = tmp_path / winrecovery.OS_INDICATIONS_VAR
    boot_to_fw_ui = 0x1
    path.write_bytes(b"\x07\x00\x00\x00" + boot_to_fw_ui.to_bytes(8, "little"))
    assert winrecovery.set_os_indications(winrecovery.START_OS_RECOVERY, str(tmp_path))
    _, value = winrecovery.read_os_indications(str(tmp_path))
    assert value == (boot_to_fw_ui | winrecovery.START_OS_RECOVERY)    # both bits, the old one not clobbered


def test_a_system_that_cannot_write_efi_variables_fails_quietly_not_loudly(tmp_path):
    """Not UEFI, firmware refuses the write, whatever the reason: this must never raise. The reboot still happens;
    it just has no extra effect, which is the whole point of treating this as best-effort."""
    missing = tmp_path / "does-not-exist-as-a-directory"
    assert winrecovery.set_os_indications(winrecovery.START_OS_RECOVERY, str(missing)) is False


def test_setting_an_already_set_bit_is_a_harmless_no_op(tmp_path):
    winrecovery.set_os_indications(winrecovery.START_OS_RECOVERY, str(tmp_path))
    assert winrecovery.set_os_indications(winrecovery.START_OS_RECOVERY, str(tmp_path))
    _, value = winrecovery.read_os_indications(str(tmp_path))
    assert value == winrecovery.START_OS_RECOVERY


# ------------------------------------------------------------------- the helper --

class FakeRun:
    def __init__(self, grub_reboot_ok: bool = True):
        self.grub_reboot_ok, self.calls = grub_reboot_ok, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        rc = 0 if argv[0] != "grub-reboot" or self.grub_reboot_ok else 1
        return subprocess.CompletedProcess(argv, rc, "", "")


def test_the_helper_refuses_to_run_unprivileged():
    assert winrecovery.helper_main(["normal"], geteuid=lambda: 1000) == 1


def test_the_helper_only_accepts_known_modes():
    assert winrecovery.helper_main(["bogus"], geteuid=lambda: 0) == 2
    assert winrecovery.helper_main([], geteuid=lambda: 0) != 2    # "" -> default "normal": valid, not an error


def test_a_normal_restart_never_touches_os_indications(monkeypatch):
    fake = FakeRun()
    calls = []
    monkeypatch.setattr(winrecovery, "set_os_indications", lambda *a, **k: calls.append(a) or True)
    assert winrecovery.helper_main(["normal"], run=fake, geteuid=lambda: 0) == 0
    assert calls == []
    assert fake.calls == [["grub-reboot", winrecovery.GRUB_WINDOWS_ENTRY], ["systemctl", "reboot"]]


def test_a_recovery_restart_sets_the_bit_between_grub_reboot_and_the_actual_reboot(monkeypatch):
    fake = FakeRun()
    order = []
    monkeypatch.setattr(winrecovery, "set_os_indications", lambda *a, **k: order.append("set_os_indications") or True)
    real_call = fake.__call__

    def tracking_call(argv, **k):
        order.append(argv[0])
        return real_call(argv, **k)
    assert winrecovery.helper_main(["recovery"], run=tracking_call, geteuid=lambda: 0) == 0
    assert order == ["grub-reboot", "set_os_indications", "systemctl"]


def test_a_failed_grub_reboot_never_reaches_the_actual_reboot():
    fake = FakeRun(grub_reboot_ok=False)
    assert winrecovery.helper_main(["recovery"], run=fake, geteuid=lambda: 0) == 3
    assert fake.calls == [["grub-reboot", winrecovery.GRUB_WINDOWS_ENTRY]]      # systemctl reboot was never called


def test_cli_goes_through_pkexec_to_the_one_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(winrecovery.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert winrecovery.main(["recovery"]) == 0
    assert calls == [["pkexec", winrecovery.HELPER, "recovery"]]


# ------------------------------------------------------------------------- wiring --

def test_the_privileged_helper_is_wired_to_the_module():
    helper = os.path.join(ROOT, "payload/usr/libexec/lintab/reboot-windows")
    assert "helper_main" in open(helper).read()


def test_the_desktop_file_opens_the_chooser_not_the_old_direct_reboot():
    desktop = open(os.path.join(ROOT, "payload/usr/share/applications/lintab-restart-windows.desktop")).read()
    assert "Exec=lintab-restart-windows-gui" in desktop
    assert "pkexec" not in desktop                    # elevation now happens per-choice inside the chooser, not here


def test_every_choice_explains_itself_honestly():
    from lintab import restartwindows_gui
    keys = [key for key, _label, _subtitle in restartwindows_gui.CHOICES]
    assert keys == ["normal", "recovery", "safe"]
    recovery_subtitle = next(s for k, _l, s in restartwindows_gui.CHOICES if k == "recovery")
    assert "Automatic Repair" in recovery_subtitle and "does not open" in recovery_subtitle  # states the real, tested effect
    safe_subtitle = next(s for k, _l, s in restartwindows_gui.CHOICES if k == "safe")
    assert "press 4" in safe_subtitle                               # the real remaining steps are stated, not hidden
