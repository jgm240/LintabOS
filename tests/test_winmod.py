# SPDX-License-Identifier: MIT
"""LinWinMod: the SAM/SECURITY refusal (the one rule that must never break), hive discovery, reading real registry
data (against a real hive built from hivex's own upstream test fixture), and the remount helper's guardrails.

Needs python3-hivex (in the builder container): ./scripts/test-features.sh
"""

import ast
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import winmod  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.hive")
needs_hivex = pytest.mark.skipif(
    __import__("importlib").util.find_spec("hivex") is None, reason="needs python3-hivex")


# ------------------------------------------------------------------- the SAM/SECURITY rule --

@pytest.mark.parametrize("path", [
    "/media/windows/Windows/System32/config/SAM",
    "/media/windows/Windows/System32/config/sam",            # case: NTFS is case-insensitive
    "/media/windows/Windows/System32/config/SECURITY",
    "SAM", "SECURITY",
    "/media/windows/Windows/System32/config/../config/SAM",  # a path that still resolves to the same basename
])
def test_sam_and_security_are_refused_by_basename_however_the_path_is_spelled(path):
    assert winmod.is_blocked_hive(path)
    with pytest.raises(winmod.BlockedHiveError):
        winmod.open_hive(path)


def test_the_refusal_explains_why_not_just_that_it_is_refused():
    with pytest.raises(winmod.BlockedHiveError, match="password hashes|credentials"):
        winmod.open_hive("/media/windows/Windows/System32/config/SAM")


@needs_hivex
def test_sam_is_refused_even_though_the_file_is_a_perfectly_valid_hive(tmp_path):
    """The block is on the name, not on whether hivex can parse the file — a real, openable hive named SAM is still refused."""
    sam = tmp_path / "SAM"
    sam.write_bytes(open(FIXTURE, "rb").read())
    with pytest.raises(winmod.BlockedHiveError):
        winmod.open_hive(str(sam))


def test_hive_discovery_never_lists_sam_or_security_even_when_present_on_disk(tmp_path):
    config = tmp_path / "Windows/System32/config"
    config.mkdir(parents=True)
    for name in ("SOFTWARE", "SYSTEM", "DEFAULT", "SAM", "SECURITY", "BCD", "COMPONENTS"):
        (config / name).write_bytes(b"x")
    found = {h.label: h.path for h in winmod.find_hives(str(tmp_path))}
    assert set(found) == {"SOFTWARE", "SYSTEM", "DEFAULT"}
    assert "SAM" not in found and "SECURITY" not in found
    for ref in winmod.find_hives(str(tmp_path)):
        assert not winmod.is_blocked_hive(ref.path)        # the discovery list and the open-time guard agree


def test_per_user_hives_are_found_and_system_profiles_are_skipped(tmp_path):
    users = tmp_path / "Users"
    for name in ("ada", "Default", "Public", "All Users"):
        (users / name).mkdir(parents=True)
    (users / "ada" / "NTUSER.DAT").write_bytes(b"x")
    (users / "ada" / "AppData/Local/Microsoft/Windows").mkdir(parents=True)
    (users / "ada" / "AppData/Local/Microsoft/Windows/UsrClass.dat").write_bytes(b"x")
    (users / "Default" / "NTUSER.DAT").write_bytes(b"x")     # the profile template, not a real account
    labels = [h.label for h in winmod.find_hives(str(tmp_path))]
    assert "ada (user)" in labels and "ada (user classes)" in labels
    assert not any("Default" in l or "Public" in l or "All Users" in l for l in labels)


def test_discovery_copes_with_a_partition_that_is_not_mounted_or_not_windows(tmp_path):
    assert winmod.find_hives(str(tmp_path / "nothing-here")) == []
    (tmp_path / "just-a-file.txt").write_text("x")
    assert winmod.find_hives(str(tmp_path)) == []


# ------------------------------------------------------------------ reading real registry data --

@needs_hivex
def test_reading_a_real_nested_key_and_its_string_values():
    h = winmod.open_hive(FIXTURE)
    node = winmod.navigate(h, ["Microsoft", "WindowsNT", "CurrentVersion", "Winlogon"])
    data = winmod.read_node(h, node)
    assert data.name == "Winlogon" and data.child_names == []
    values = {v.name: v for v in data.values}
    assert values["AutoAdminLogon"].type_name == "REG_SZ" and values["AutoAdminLogon"].display == "0"
    assert values["DefaultUserName"].display == "ada"


@needs_hivex
def test_every_value_type_renders_to_something_readable():
    h = winmod.open_hive(FIXTURE)
    values = {v.name: v for v in winmod.read_node(h, winmod.navigate(h, ["LintabTest"])).values}
    assert values[""].type_name == "REG_SZ" and values[""].display == "default value"       # the unnamed/default value
    assert values["Answer"].type_name == "REG_DWORD" and "42" in values["Answer"].display
    assert values["Big"].type_name == "REG_QWORD" and "123456789012" in values["Big"].display
    assert values["List"].type_name == "REG_MULTI_SZ" and values["List"].display == "one\ntwo\nthree"
    assert values["Raw"].type_name == "REG_BINARY" and values["Raw"].display.startswith("00 01 02 03")


@needs_hivex
def test_an_empty_key_is_read_without_error():
    h = winmod.open_hive(FIXTURE)
    data = winmod.read_node(h, winmod.navigate(h, ["Microsoft", "WindowsNT", "CurrentVersion", "EmptyKey"]))
    assert data.child_names == [] and data.values == []


@needs_hivex
def test_navigating_a_path_that_does_not_exist_names_the_missing_part():
    h = winmod.open_hive(FIXTURE)
    with pytest.raises(KeyError) as err:
        winmod.navigate(h, ["Microsoft", "DoesNotExist", "Winlogon"])
    assert err.value.args[0] == "DoesNotExist"


@needs_hivex
def test_search_finds_keys_by_name_case_insensitively_and_is_capped():
    h = winmod.open_hive(FIXTURE)
    assert "Microsoft\\WindowsNT" in winmod.search_keys(h, "windowsnt")
    assert any(m.endswith("Winlogon") for m in winmod.search_keys(h, "LOGON"))
    assert winmod.search_keys(h, "does-not-exist-anywhere") == []
    assert len(winmod.search_keys(h, "", limit=3)) <= 3               # an empty needle matches everything: the cap holds


@needs_hivex
def test_a_binary_value_too_long_to_show_in_full_is_truncated_not_hung():
    text = winmod._hex_preview(bytes(range(256)) * 4, limit=64)
    assert text.endswith("…") and len(text.split()) <= 65


# ------------------------------------------------------------------- the remount helper --

class FakeRun:
    def __init__(self, results=None):
        self.results, self.calls = results or {}, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        key = " ".join(argv[:2])
        rc, out = self.results.get(key, (0, ""))
        return subprocess.CompletedProcess(argv, rc, out, "")


def test_the_helper_refuses_to_run_unprivileged():
    assert winmod.remount_helper_main(["rw"], geteuid=lambda: 1000) == 1


def test_the_helper_only_accepts_the_two_known_arguments():
    assert winmod.remount_helper_main([], geteuid=lambda: 0) == 2
    assert winmod.remount_helper_main(["rm", "-rf", "/"], geteuid=lambda: 0) == 2
    assert winmod.remount_helper_main(["RW"], geteuid=lambda: 0) == 2  # exact match only, no case folding


def test_the_helper_refuses_a_mount_point_lintab_windows_files_never_declared(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=root / ext4 defaults 0 1\n")
    run = FakeRun()
    assert winmod.remount_helper_main(["rw"], fstab_path=str(fstab), run=run, geteuid=lambda: 0) == 3
    assert run.calls == []                                            # it never ran mount at all


def test_the_helper_only_ever_touches_the_one_fstab_declared_mount_point(tmp_path):
    from lintab import winfiles
    fstab = tmp_path / "fstab"
    fstab.write_text(winfiles.BEGIN + "\nUUID=X /media/windows ntfs3 ro,noauto 0 0\n" + winfiles.END + "\n")
    run = FakeRun()
    assert winmod.remount_helper_main(["rw"], fstab_path=str(fstab), run=run, geteuid=lambda: 0) == 0
    assert run.calls == [["mount", "/media/windows"], ["mount", "-o", "remount,rw", "/media/windows"]]
    assert all("/media/windows" in c or c[0] == "mount" for c in run.calls)
    for call in run.calls:
        assert not any(a not in ("mount", "-o", "remount,rw", "remount,ro", "/media/windows") for a in call)


def test_an_already_mounted_point_is_remounted_without_mounting_it_again(tmp_path, monkeypatch):
    from lintab import winfiles
    fstab = tmp_path / "fstab"
    fstab.write_text(winfiles.BEGIN + "\n" + winfiles.END + "\n")
    monkeypatch.setattr(winmod, "mount_status", lambda *a, **k: (True, False))
    run = FakeRun()
    assert winmod.remount_helper_main(["ro"], fstab_path=str(fstab), run=run, geteuid=lambda: 0) == 0
    assert run.calls == [["mount", "-o", "remount,ro", "/media/windows"]]


def test_a_failed_mount_or_remount_is_reported_not_swallowed(tmp_path, capsys):
    from lintab import winfiles
    fstab = tmp_path / "fstab"
    fstab.write_text(winfiles.BEGIN + "\n" + winfiles.END + "\n")
    run = FakeRun({"mount /media/windows": (32, "mount: special device not found")})
    assert winmod.remount_helper_main(["rw"], fstab_path=str(fstab), run=run, geteuid=lambda: 0) == 4
    assert "special device not found" in capsys.readouterr().err


def test_mount_status_reads_the_real_mount_table():
    proc_mounts = "\n".join([
        "UUID=root / ext4 rw,relatime 0 0",
        "/dev/sda3 /media/windows ntfs3 ro,relatime,uid=1000 0 0",
    ])
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".mounts") as f:
        f.write(proc_mounts)
        f.flush()
        assert winmod.mount_status("/media/windows", f.name) == (True, False)
        assert winmod.mount_status("/not/mounted", f.name) == (False, False)


def test_ensure_writable_and_read_only_go_through_pkexec_and_the_one_helper_path():
    calls = []
    winmod.ensure_writable(run=lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    winmod.ensure_read_only(run=lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    assert calls == [["pkexec", winmod.REMOUNT_HELPER, "rw"], ["pkexec", winmod.REMOUNT_HELPER, "ro"]]


# ------------------------------------------------------------------------- wiring --

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_the_privileged_helper_and_polkit_policy_are_wired_together():
    helper = os.path.join(ROOT, "payload/usr/libexec/lintab/winmod-remount")
    assert "remount_helper_main" in open(helper).read()
    policy = open(os.path.join(ROOT, "payload/usr/share/polkit-1/actions/org.lintabos.winmod.policy")).read()
    assert "/usr/libexec/lintab/winmod-remount" in policy and "auth_admin" in policy


def test_no_write_capable_hivex_call_exists_anywhere_in_the_module():
    source = open(os.path.join(ROOT, "installer/lintab/winmod.py")).read()
    assert "write=True" not in source and "node_set_value" not in source and "node_add_child" not in source
    assert "write=False" in source                           # the open call is explicit about it, not just the default


def test_every_background_worker_in_the_gui_catches_its_own_exceptions():
    """A worker that raises before calling GLib.idle_add leaves its page stuck on its initial placeholder text forever —
    exactly the bug a real user hit (LinWinMod stuck on "Looking for Windows..."): windows_partitions() raised (disk
    enumeration has already failed with a real DiskError in this project's own test logs), the background thread died
    silently, and the callback that updates the label never ran. Every function that calls GLib.idle_add must wrap
    whatever it computes beforehand in a try/except that still reaches idle_add on failure."""
    source = open(os.path.join(ROOT, "installer/lintab/winmod_gui.py")).read()
    tree = ast.parse(source)

    def calls_idle_add(node) -> bool:
        return any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "idle_add" for n in ast.walk(node))

    def has_try(node) -> bool:
        return any(isinstance(n, ast.Try) for n in ast.walk(node))

    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and calls_idle_add(node):
            assert has_try(node), f"{node.name} calls GLib.idle_add without a try/except around it"
            checked += 1
    assert checked >= 4   # _scan, _open's work(), _checked's work(), _scan_hives — also fails if one of them is removed
