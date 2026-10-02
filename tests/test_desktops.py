# SPDX-License-Identifier: MIT
"""Tests for the optional KDE/Xfce desktops: what gets installed, in what order, how failure is handled, and the touch
settings and rotation maths. The real downloads and the real desktops are not exercised here."""

import io
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import desktops, install as installmod, tablet, touchsetup, xfce_rotate  # noqa: E402


# ------------------------------------------------------------------ choosing --

def test_selection_is_validated_deduplicated_and_ordered():
    assert [d.key for d in desktops.parse_selection(["xfce", "kde", "kde"])] == ["kde", "xfce"]
    assert desktops.parse_selection([]) == []
    with pytest.raises(ValueError, match="cinnamon"):
        desktops.parse_selection(["kde", "cinnamon"])
    assert desktops.download_summary([]) == ""
    assert "KDE Plasma (about 450 MB)" in desktops.download_summary(["kde"])


def test_config_rejects_unknown_desktop_names(tmp_path):
    cfg = installmod.InstallConfig(plan=None, fullname="A", username="a", password="x", desktops=("kde", "gnomeish"))
    with pytest.raises(installmod.InstallError, match="gnomeish"):
        cfg.validate()


def test_package_lists_have_what_makes_each_desktop_usable_by_touch():
    kde, xfce = desktops.DESKTOPS["kde"].packages, desktops.DESKTOPS["xfce"].packages
    assert "maliit-keyboard" in kde and "kwin-wayland" in kde and "kscreen" in kde
    for needed in ("onboard", "iio-sensor-proxy", "xinput", "x11-xserver-utils", "mate-polkit", "xserver-xorg-core"):
        assert needed in xfce                                    # keyboard, rotation, and the pieces Xfce lacks in GNOME's image
    assert not {"sddm", "lightdm"} & set(kde + xfce)             # never bring a second login screen


# ----------------------------------------------------------------- apt output --

def test_apt_status_lines_become_phase_and_percent():
    assert desktops.parse_apt_status("dlstatus:1:23.5:Downloading file") == ("download", 23.5)
    assert desktops.parse_apt_status("pmstatus:xfwm4:80:Installing xfwm4") == ("install", 80.0)
    for junk in ("", "Reading package lists...", "dlstatus:oops", "pmstatus:x:notanumber:y"):
        assert desktops.parse_apt_status(junk) is None


# --------------------------------------------------------------- the install --

class FakeApt:
    def __init__(self, lines, code=0):
        self.lines, self.code = lines, code
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))

    def wait(self):
        return self.code


def run_install(tmp_path, keys, apt_lines=None, code=0, chroot_fail=None, dm="/usr/sbin/gdm3", sessions=True):
    target = tmp_path / "target"
    (target / "etc/X11").mkdir(parents=True, exist_ok=True)
    (target / "etc/X11/default-display-manager").write_text(dm + "\n")
    (target / "etc").mkdir(exist_ok=True)
    (target / "etc/resolv.conf").write_text("nameserver 10.0.0.1\n")
    host = tmp_path / "host-resolv"
    host.write_text("nameserver 192.168.1.1\n")
    if sessions:
        for rel in ("usr/share/wayland-sessions/plasma.desktop", "usr/share/xsessions/xfce.desktop"):
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_text("[Desktop Entry]\n")
    events, progress = [], []

    def chroot(argv, input=None):
        events.append(("chroot", argv, input, (target / "etc/resolv.conf").read_text()))
        if chroot_fail and argv[:2] == chroot_fail:
            raise installmod.InstallError("apt-get update failed: could not resolve deb.debian.org")

    def stream(argv):
        events.append(("stream", argv, None, (target / "etc/resolv.conf").read_text()))
        return FakeApt(apt_lines or [], code)

    real = desktops.working_dns
    desktops.working_dns = lambda t, host_resolv=str(host): real(t, host_resolv)
    try:
        warnings = desktops.install_desktops(keys, str(target), chroot, stream, lambda f, m: progress.append((f, m)), 0.94, 0.985)
    finally:
        desktops.working_dns = real
    return warnings, events, progress, target


def test_nothing_chosen_means_nothing_happens(tmp_path):
    warnings, events, progress, _ = run_install(tmp_path, [])
    assert warnings == [] and events == [] and progress == []


def test_install_order_keeps_gdm_and_uses_the_live_dns_only_while_downloading(tmp_path):
    lines = ["Reading package lists", "dlstatus:1:50:Downloading", "dlstatus:1:100:Downloading", "pmstatus:x:50:Installing"]
    warnings, events, progress, target = run_install(tmp_path, ["kde", "xfce"], lines)
    assert warnings == []
    kinds = [(e[0], e[1][0]) for e in events]
    assert kinds[0] == ("chroot", "debconf-set-selections")
    assert "shared/default-x-display-manager\tselect\tgdm3" in events[0][2]                 # GDM is chosen BEFORE any dm package
    assert kinds[1:3] == [("chroot", "apt-get"), ("stream", "apt-get")]
    assert events[1][1] == ["apt-get", "update"]
    install_cmd = events[2][1]
    assert "--no-install-recommends" in install_cmd and "kde-plasma-desktop" in install_cmd and "onboard" in install_cmd
    assert events[1][3] == events[2][3] == "nameserver 192.168.1.1\n"                       # live DNS while downloading
    assert (target / "etc/resolv.conf").read_text() == "nameserver 10.0.0.1\n"              # the original is back afterwards
    fractions = [f for f, _ in progress]
    assert fractions == sorted(fractions) and 0.94 <= fractions[0] and fractions[-1] == pytest.approx(0.985)
    assert any("Downloading" in m for _, m in progress) and any("Installing" in m for _, m in progress)


def test_a_symlinked_resolv_conf_is_restored_as_a_symlink(tmp_path):
    target = tmp_path / "t"
    (target / "etc").mkdir(parents=True)
    os.symlink("../run/systemd/resolve/stub-resolv.conf", target / "etc/resolv.conf")
    host = tmp_path / "h"
    host.write_text("nameserver 1.1.1.1\n")
    with desktops.working_dns(str(target), str(host)):
        assert (target / "etc/resolv.conf").read_text() == "nameserver 1.1.1.1\n" and not os.path.islink(target / "etc/resolv.conf")
    assert os.readlink(target / "etc/resolv.conf") == "../run/systemd/resolve/stub-resolv.conf"


def test_if_a_desktop_swaps_the_login_screen_gdm_is_put_back(tmp_path):
    warnings, events, _, target = run_install(tmp_path, ["kde"], dm="/usr/bin/sddm")
    assert (target / "etc/X11/default-display-manager").read_text() == "/usr/sbin/gdm3\n"
    assert any(e[1][:2] == ["ln", "-sf"] and "gdm3.service" in e[1][2] for e in events)
    assert any("GDM was put back" in w for w in warnings)


def test_a_failed_download_is_a_warning_not_a_failed_install(tmp_path):
    warnings, *_ = run_install(tmp_path, ["xfce"], chroot_fail=["apt-get", "update"])
    assert len(warnings) == 1 and "Xfce" in warnings[0] and "LintabOS itself is installed" in warnings[0]
    assert "sudo apt install xfce4" in warnings[0]
    warnings, *_ = run_install(tmp_path, ["kde"], ["E: Unable to fetch some archives"], code=100)
    assert len(warnings) == 1 and "Unable to fetch" in warnings[0]


def test_missing_session_files_are_reported(tmp_path):
    warnings, *_ = run_install(tmp_path, ["kde"], sessions=False)
    assert any("login session wasn't found" in w for w in warnings)


# ------------------------------------------------------------ touch settings --

def test_xfce_touch_settings_are_complete_and_well_formed():
    settings = {(s.channel, s.prop): s for s in touchsetup.xfce_settings()}
    assert settings[("xfce4-panel", "/panels/panel-1/size")].value == "48"
    assert settings[("xsettings", "/Xft/DPI")].value == "144" and settings[("xsettings", "/Gtk/CursorThemeSize")].value == "32"
    assert all(s.kind in ("int", "bool") for s in settings.values())
    commands = touchsetup.xfce_commands()
    assert ["gsettings", "set", "org.onboard.auto-show", "enabled", "true"] in commands
    assert all(c[0] in ("xfconf-query", "gsettings") for c in commands)


def test_kde_touch_settings_turn_on_maliit_and_grow_the_panel():
    flat = [" ".join(c) for c in touchsetup.kde_commands()]
    assert any("kwinrc" in c and "InputMethod" in c and "com.github.maliit.keyboard.desktop" in c for c in flat)
    assert any("VirtualKeyboardEnabled true" in c for c in flat)
    command = touchsetup.kde_panel_command()
    assert "org.kde.PlasmaShell.evaluateScript" in command and "p.height = 56" in command[-1]


def test_kde_waits_for_the_shell_and_only_counts_as_done_if_everything_worked():
    ran = []
    assert touchsetup.apply("kde", run=lambda c: ran.append(c) or 0, wait=lambda: True) is True
    assert ran[-1][0] == "dbus-send"
    ran.clear()
    assert touchsetup.apply("kde", run=lambda c: ran.append(c) or 0, wait=lambda: False) is False   # shell never appeared
    assert not any(c[0] == "dbus-send" for c in ran)                                                  # (so no panel command either)
    assert touchsetup.apply("xfce", run=lambda c: 1 if c[0] == "gsettings" else 0) is False           # one failing step => retry later


def test_wait_for_polls_until_true_or_times_out():
    answers = iter([False, False, True])
    ticks = []
    assert touchsetup.wait_for(lambda: next(answers), 90, sleep=ticks.append, clock=lambda: 0.0) is True and len(ticks) == 2
    now = [0.0]
    assert touchsetup.wait_for(lambda: False, 10, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0]) is False


def test_setup_runs_once_per_user_and_can_be_forced(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(touchsetup, "apply", lambda desktop: calls.append(desktop) or True)
    assert touchsetup.main(["xfce"]) == 0 and calls == ["xfce"]
    assert os.path.exists(touchsetup.marker_path("xfce"))
    assert touchsetup.main(["xfce"]) == 0 and calls == ["xfce"]                # second login: nothing
    assert touchsetup.main(["xfce", "--force"]) == 0 and calls == ["xfce", "xfce"]
    monkeypatch.setattr(touchsetup, "apply", lambda desktop: False)
    assert touchsetup.main(["kde"]) == 1 and not os.path.exists(touchsetup.marker_path("kde"))   # failure: retried next time


# ------------------------------------------------------------- Xfce rotation --

def test_orientation_lines_from_monitor_sensor():
    assert xfce_rotate.parse_orientation("=== Has accelerometer (orientation: normal)") == "normal"
    assert xfce_rotate.parse_orientation("    Accelerometer orientation changed: left-up") == "left-up"
    assert xfce_rotate.parse_orientation("    Accelerometer orientation changed: undefined") is None
    assert xfce_rotate.parse_orientation("Light changed: 12 (lux)") is None


def test_every_orientation_has_a_rotation_and_a_matching_touch_matrix():
    assert set(xfce_rotate.ROTATIONS) == {"normal", "bottom-up", "left-up", "right-up"}
    for rotate, matrix in xfce_rotate.ROTATIONS.values():
        assert rotate in ("normal", "inverted", "left", "right") and len(matrix.split()) == 9
    # the touch matrix must undo the same turn the screen made: inverted flips both axes
    assert xfce_rotate.ROTATIONS["bottom-up"][1] == "-1 0 1 0 -1 1 0 0 1"


def test_commands_rotate_the_screen_and_every_touch_device():
    cmds = xfce_rotate.commands("left-up", "eDP-1", ["11", "14"])
    assert cmds[0] == ["xrandr", "--output", "eDP-1", "--rotate", "right"]
    assert cmds[1][:4] == ["xinput", "set-prop", "11", "Coordinate Transformation Matrix"] and len(cmds) == 3


def test_output_and_touch_device_discovery():
    xrandr = "Screen 0: minimum 320 x 200\nHDMI-1 disconnected\neDP-1 connected primary 1200x2000+0+0 (normal) 0mm x 0mm\n"
    assert xfce_rotate.primary_output(xrandr) == "eDP-1"
    assert xfce_rotate.primary_output("DP-2 connected 1920x1080+0+0\nDP-3 connected 800x600+0+0\n") == "DP-2"
    assert xfce_rotate.primary_output("") is None
    xinput = ("⎡ Virtual core pointer                    \tid=2\t[master pointer  (3)]\n"
              "⎜   ↳ ELAN902C:00 04F3:2C01                    \tid=11\t[slave  pointer  (2)]\n"
              "⎜   ↳ ELAN902C:00 04F3:2C01 Stylus             \tid=12\t[slave  pointer  (2)]\n"
              "⎜   ↳ SYNA Touchpad                            \tid=13\t[slave  pointer  (2)]\n"
              "⎜   ↳ Lenovo Duet Keyboard Mouse               \tid=14\t[slave  pointer  (2)]\n"
              "⎣ Virtual core keyboard                   \tid=3\t[master keyboard (2)]\n")
    assert xfce_rotate.touch_device_ids(xinput) == ["11", "12"]                  # touchpad and mouse are left alone


# --------------------------------------------- tablet mode in each desktop --

def test_desktop_detection_uses_the_colon_separated_session_variable():
    assert tablet.detect_desktop({"XDG_CURRENT_DESKTOP": "KDE"}) == "kde"
    assert tablet.detect_desktop({"XDG_CURRENT_DESKTOP": "XFCE"}) == "xfce"
    assert tablet.detect_desktop({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == "gnome"
    assert tablet.detect_desktop({}) == "gnome"


def test_on_screen_keyboard_commands_per_desktop():
    kde = tablet.osk_commands("tablet", "kde")
    assert kde[0][-1] == "true" and "variant:boolean:true" in kde[1][-1] and "org.kde.KWin" in " ".join(kde[1])
    assert tablet.osk_commands("laptop", "kde")[1][-1] == "variant:boolean:false"
    assert tablet.osk_commands("tablet", "xfce") == [["gsettings", "set", "org.onboard.auto-show", "enabled", "true"]]
    laptop = tablet.osk_commands("laptop", "xfce")
    assert laptop[0][-1] == "false" and "org.onboard.Onboard.Keyboard.Hide" in laptop[1]    # also hides it right now
    assert tablet.osk_commands("tablet", "gnome")[0][:3] == ["gsettings", "set", "org.gnome.desktop.a11y.applications"]


def test_a_missing_tool_does_not_crash_the_service():
    def run(cmd):
        raise FileNotFoundError(cmd[0])
    assert tablet.apply_state("tablet", run=run, desktop="kde")                     # returns the commands, no exception
