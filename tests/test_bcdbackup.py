# SPDX-License-Identifier: MIT
"""lintab.bcdbackup: the power gate, the hive write/verify round-trip, and that a failed verification always
restores the backup before returning.

The fixture (tests/fixtures/synthetic-bcd-sample/fake-bcd) is built from tests/fixtures/sample.hive - itself
hivex's own upstream open-source test fixture, not derived from any real Windows installation - with a synthetic
BCD-shaped Objects\\{bootmgr}\\Elements structure added on top via hivex's own write API (the same one
installer/lintab/bcdbackup.py uses). See scripts/gen-synthetic-bcd-fixture.py for exactly how it was built.
The GUIDs and element IDs inside it are either UEFI-spec-constant identifiers (not copyrightable expression) or
made up for this fixture - nothing here is extracted from a real Microsoft binary.

The write/verify tests need python3-hivex (in the builder container): ./scripts/test-features.sh
"""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import bcdbackup  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "synthetic-bcd-sample", "fake-bcd")
KNOWN_DEFAULT_GUID = "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"   # made up for this fixture - see module docstring
needs_hivex = pytest.mark.skipif(
    __import__("importlib").util.find_spec("hivex") is None, reason="needs python3-hivex")


# ----------------------------------------------------------------------------------------------- the power gate --

def _write_supply(root, name: str, kind: str, **attrs):
    d = root / name
    d.mkdir()
    (d / "type").write_text(kind)
    for key, value in attrs.items():
        (d / key).write_text(str(value))


def test_no_power_supplies_at_all_means_cannot_tell_not_fine(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert bcdbackup.ac_connected(str(missing)) is None
    ok, reason = bcdbackup.power_safe_to_write(str(missing))
    assert ok is False and "power" in reason.lower()


def test_on_battery_with_no_charger_refuses(tmp_path):
    _write_supply(tmp_path, "BAT0", "Battery", capacity=90)
    _write_supply(tmp_path, "AC0", "Mains", online=0)
    ok, reason = bcdbackup.power_safe_to_write(str(tmp_path))
    assert ok is False and "charger" in reason.lower()


def test_on_charger_but_battery_below_minimum_refuses(tmp_path):
    _write_supply(tmp_path, "BAT0", "Battery", capacity=39)
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    ok, reason = bcdbackup.power_safe_to_write(str(tmp_path))
    assert ok is False and "39%" in reason


def test_on_charger_at_exactly_the_minimum_is_fine(tmp_path):
    _write_supply(tmp_path, "BAT0", "Battery", capacity=40)
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    ok, reason = bcdbackup.power_safe_to_write(str(tmp_path))
    assert ok is True and reason == ""


def test_usb_c_charging_counts_as_a_charger_not_just_mains(tmp_path):
    _write_supply(tmp_path, "BAT0", "Battery", capacity=100)
    _write_supply(tmp_path, "usb0", "USB_PD", online=1)
    assert bcdbackup.ac_connected(str(tmp_path)) is True


def test_no_battery_reported_at_all_does_not_block_a_connected_charger(tmp_path):
    """Some devices report no Battery node at all while genuinely plugged in - absence of a reading must not be
    treated as "below the minimum"."""
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    ok, _reason = bcdbackup.power_safe_to_write(str(tmp_path))
    assert ok is True


# ------------------------------------------------------------------------------------------- real-hive write/verify --

@needs_hivex
def test_the_default_object_guid_resolves_from_the_real_sample(tmp_path):
    import hivex
    h = hivex.Hivex(FIXTURE, write=False)
    assert bcdbackup._default_object_guid(h) == KNOWN_DEFAULT_GUID


@needs_hivex
def test_the_element_does_not_exist_before_any_write(tmp_path):
    assert bcdbackup.verify_onetime_advanced_options(FIXTURE, expected=True) is False


@needs_hivex
def test_write_then_fresh_reopen_verifies_the_exact_encoding(tmp_path):
    """The same round-trip proved out manually during development: REG_BINARY, single byte, 0x01 for true -
    confirmed here through a second, independent hivex handle, not the one that wrote it."""
    copy = tmp_path / "bcd-write-test"
    shutil.copy2(FIXTURE, copy)
    os.chmod(copy, 0o644)
    bcdbackup.set_onetime_advanced_options(str(copy), enabled=True)
    assert bcdbackup.verify_onetime_advanced_options(str(copy), expected=True) is True

    import hivex
    h = hivex.Hivex(str(copy), write=False)
    guid = bcdbackup._default_object_guid(h)
    elements = bcdbackup._target_elements_node(h, guid)
    element = h.node_get_child(elements, bcdbackup.ONETIME_ADVANCED_OPTIONS_ELEMENT)
    value = bcdbackup._find_value(h, element, "Element")
    assert h.value_type(value) == (3, 1) and h.value_value(value)[1] == b"\x01"


@needs_hivex
def test_writing_does_not_disturb_the_original_sibling_elements(tmp_path):
    copy = tmp_path / "bcd-write-test"
    shutil.copy2(FIXTURE, copy)
    os.chmod(copy, 0o644)

    import hivex
    before = hivex.Hivex(str(copy), write=False)
    guid = bcdbackup._default_object_guid(before)
    elements_before = {before.node_name(c) for c in before.node_children(
        bcdbackup._target_elements_node(before, guid))}

    bcdbackup.set_onetime_advanced_options(str(copy), enabled=True)

    after = hivex.Hivex(str(copy), write=False)
    elements_after = {after.node_name(c) for c in after.node_children(bcdbackup._target_elements_node(after, guid))}
    assert elements_before <= elements_after                      # nothing original was lost
    assert bcdbackup.ONETIME_ADVANCED_OPTIONS_ELEMENT in elements_after    # the new one is there


@needs_hivex
def test_verify_is_false_not_an_exception_for_a_file_that_is_not_a_hive_at_all(tmp_path):
    garbage = tmp_path / "not-a-hive"
    garbage.write_bytes(b"not a real BCD file")
    assert bcdbackup.verify_onetime_advanced_options(str(garbage)) is False


@needs_hivex
def test_set_raises_a_clear_structure_error_on_a_hive_that_is_not_a_bcd(tmp_path):
    """A structurally-unrelated real hive (not a BCD at all) must fail loudly with BcdStructureError, not write
    to the wrong place or silently do nothing."""
    sample_hive = os.path.join(os.path.dirname(__file__), "fixtures", "sample.hive")
    copy = tmp_path / "sample-copy.hive"
    shutil.copy2(sample_hive, copy)
    os.chmod(copy, 0o644)
    with pytest.raises(bcdbackup.BcdStructureError):
        bcdbackup.set_onetime_advanced_options(str(copy))


# --------------------------------------------------------------------------------------------------- orchestration --

class FakeRun:
    def __init__(self, mount_ok: bool = True):
        self.mount_ok, self.calls = mount_ok, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        rc = 0 if argv[0] != "mount" or self.mount_ok else 1
        return subprocess.CompletedProcess(argv, rc, "", "" if rc == 0 else "mount failed")


def _fake_esp():
    class Esp:
        path = "/dev/fake-esp1"
    return Esp()


def test_the_power_gate_runs_before_anything_else_is_even_looked_at(monkeypatch, tmp_path):
    missing = tmp_path / "no-such-dir"
    called = []
    monkeypatch.setattr(bcdbackup, "find_windows_esp", lambda *a, **k: called.append("find_esp") or _fake_esp())
    result = bcdbackup.write_onetime_advanced_options_safely(power_supply_dir=str(missing))
    assert result.ok is False
    assert called == []                     # never got far enough to look for the ESP at all


def test_no_windows_esp_found_is_reported_not_crashed(monkeypatch, tmp_path):
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    monkeypatch.setattr(bcdbackup, "find_windows_esp", lambda *a, **k: None)
    result = bcdbackup.write_onetime_advanced_options_safely(power_supply_dir=str(tmp_path))
    assert result.ok is False and "efi system partition" in result.message.lower()


def test_a_backup_failure_refuses_to_write_at_all(monkeypatch, tmp_path):
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    monkeypatch.setattr(bcdbackup, "find_windows_esp", lambda *a, **k: _fake_esp())

    esp_mount = tmp_path / "esp"
    (esp_mount / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (esp_mount / bcdbackup.BCD_SUBPATH).write_bytes(b"fake bcd contents")

    class FakeMountedEsp:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return str(esp_mount)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(bcdbackup, "MountedEsp", FakeMountedEsp)
    write_called = []
    monkeypatch.setattr(bcdbackup, "backup_bcd", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(bcdbackup, "set_onetime_advanced_options", lambda *a, **k: write_called.append(True))

    result = bcdbackup.write_onetime_advanced_options_safely(power_supply_dir=str(tmp_path))
    assert result.ok is False and write_called == []    # the write itself must never have been attempted


def test_a_failed_verification_automatically_restores_the_backup(monkeypatch, tmp_path):
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    monkeypatch.setattr(bcdbackup, "find_windows_esp", lambda *a, **k: _fake_esp())

    esp_mount = tmp_path / "esp"
    (esp_mount / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (esp_mount / bcdbackup.BCD_SUBPATH).write_bytes(b"fake bcd contents")

    class FakeMountedEsp:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return str(esp_mount)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(bcdbackup, "MountedEsp", FakeMountedEsp)
    monkeypatch.setattr(bcdbackup, "backup_bcd", lambda *a, **k: None)
    monkeypatch.setattr(bcdbackup, "set_onetime_advanced_options", lambda *a, **k: None)
    monkeypatch.setattr(bcdbackup, "verify_onetime_advanced_options", lambda *a, **k: False)   # torn write
    restore_calls = []
    monkeypatch.setattr(bcdbackup, "restore_bcd", lambda *a, **k: restore_calls.append(True))

    result = bcdbackup.write_onetime_advanced_options_safely(power_supply_dir=str(tmp_path))
    assert result.ok is False and result.restored is True
    assert restore_calls == [True]          # restore happened exactly once, automatically


def test_a_write_exception_also_triggers_restore(monkeypatch, tmp_path):
    _write_supply(tmp_path, "AC0", "Mains", online=1)
    monkeypatch.setattr(bcdbackup, "find_windows_esp", lambda *a, **k: _fake_esp())

    esp_mount = tmp_path / "esp"
    (esp_mount / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (esp_mount / bcdbackup.BCD_SUBPATH).write_bytes(b"fake bcd contents")

    class FakeMountedEsp:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return str(esp_mount)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(bcdbackup, "MountedEsp", FakeMountedEsp)
    monkeypatch.setattr(bcdbackup, "backup_bcd", lambda *a, **k: None)
    monkeypatch.setattr(bcdbackup, "set_onetime_advanced_options",
                        lambda *a, **k: (_ for _ in ()).throw(bcdbackup.BcdStructureError("unexpected")))
    restore_calls = []
    monkeypatch.setattr(bcdbackup, "restore_bcd", lambda *a, **k: restore_calls.append(True))

    result = bcdbackup.write_onetime_advanced_options_safely(power_supply_dir=str(tmp_path))
    assert result.ok is False and result.restored is True and restore_calls == [True]


def test_restore_without_a_backup_refuses_instead_of_restoring_garbage(tmp_path):
    result = bcdbackup.restore_backup_now(backup_path=str(tmp_path / "no-backup-here"))
    assert result.ok is False and "no backup" in result.message.lower()


# --------------------------------------------------------------------------------------------------------- helper --

def test_the_helper_refuses_to_run_unprivileged():
    assert bcdbackup.helper_main(["write"], geteuid=lambda: 1000) == 1


def test_the_helper_only_accepts_known_modes():
    assert bcdbackup.helper_main(["bogus"], geteuid=lambda: 0) == 2


def test_cli_goes_through_pkexec_to_the_one_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(bcdbackup.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert bcdbackup.main(["write"]) == 0
    assert calls == [["pkexec", bcdbackup.HELPER, "write"]]


# ------------------------------------------------------------------------------------------------------- wiring --

def test_the_privileged_helper_and_polkit_policy_are_wired_together():
    helper = os.path.join(ROOT, "payload/usr/libexec/lintab/bcd-write")
    assert "helper_main" in open(helper).read()
    policy = open(os.path.join(ROOT, "payload/usr/share/polkit-1/actions/org.lintabos.bcd-write.policy")).read()
    assert "/usr/libexec/lintab/bcd-write" in policy and "auth_admin" in policy


def test_the_desktop_file_is_clearly_labeled_experimental_and_opens_the_right_gui():
    desktop = open(os.path.join(ROOT, "payload/usr/share/applications/lintab-bcd-write.desktop")).read()
    assert "Exec=lintab-bcd-write-gui" in desktop
    assert "Experimental" in desktop


def test_the_gui_shows_the_experimental_warning():
    source = open(os.path.join(ROOT, "installer/lintab/bcdwrite_gui.py")).read()
    assert "experimental" in source.lower() and "not yet" in source.lower()
