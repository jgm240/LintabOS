# SPDX-License-Identifier: MIT
"""Tests for tablet mode, school mode, Windows files in the sidebar, and the touch boot menu.

These test the decision logic and the files each feature writes, using fake udev/efivars/fstab data. What can't be
tested here is the hardware itself (a real keyboard being detached, a real camera, a real touch screen).
"""

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import bootmenu, school, tablet, winfiles  # noqa: E402

# ---------------------------------------------------------------- tablet mode --

FOLIO = {"ID_INPUT_KEYBOARD": "1", "ID_BUS": "usb", "NAME": '"Lenovo Duet 3 Keyboard"', "DEVPATH": "/devices/pci0000:00/usb1/1-3/input/input9"}
BT_KEYBOARD = {"ID_INPUT_KEYBOARD": "1", "ID_BUS": "bluetooth", "NAME": '"Keychron K2"', "DEVPATH": "/devices/virtual/misc/uhid/input20"}
TOUCHSCREEN = {"ID_INPUT_TOUCHSCREEN": "1", "ID_BUS": "i2c", "NAME": '"ELAN Touchscreen"', "DEVPATH": "/devices/i2c/input3"}
POWER_BUTTON = {"ID_INPUT_KEY": "1", "ID_BUS": "", "NAME": '"Power Button"', "DEVPATH": "/devices/LNXSYSTM:00/input0"}
VIRTUAL_KBD = {"ID_INPUT_KEYBOARD": "1", "ID_BUS": "usb", "NAME": '"ydotoold virtual device"', "DEVPATH": "/devices/virtual/input/input30"}
CONSUMER = {"ID_INPUT_KEYBOARD": "1", "ID_BUS": "usb", "NAME": '"Lenovo Duet 3 Keyboard Consumer Control"', "DEVPATH": "/devices/usb/input/input10"}


def test_keyboards_are_recognised_and_other_input_devices_ignored():
    assert tablet.is_external_keyboard(FOLIO)
    assert tablet.is_external_keyboard({**BT_KEYBOARD, "DEVPATH": "/devices/pci/bluetooth/input20"})
    assert not tablet.is_external_keyboard(TOUCHSCREEN)
    assert not tablet.is_external_keyboard(POWER_BUTTON)
    assert not tablet.is_external_keyboard(VIRTUAL_KBD)          # software-made keyboards don't count
    assert not tablet.is_external_keyboard(CONSUMER)             # media-key nodes alone are not a keyboard


def test_mode_follows_the_keyboard_and_manual_choice_wins():
    assert tablet.decide("auto", [TOUCHSCREEN, POWER_BUTTON]) == "tablet"
    assert tablet.decide("auto", [TOUCHSCREEN, FOLIO]) == "laptop"
    assert tablet.decide("tablet", [FOLIO]) == "tablet"          # forced
    assert tablet.decide("laptop", []) == "laptop"
    assert tablet.decide("auto", []) == "tablet"                 # nothing typeable: touch-first


def test_the_on_screen_keyboard_setting_matches_the_state():
    issued = []
    tablet.apply_state("tablet", run=issued.append)
    assert issued == [["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-keyboard-enabled", "true"]]
    issued.clear()
    tablet.apply_state("laptop", run=issued.append)
    assert issued[0][-1] == "false"


def test_mode_setting_round_trips_and_ignores_garbage(tmp_path):
    conf = str(tmp_path / "tablet-mode.conf")
    assert tablet.read_mode(conf) == "auto"                      # missing file
    tablet.write_mode("tablet", conf)
    assert tablet.read_mode(conf) == "tablet"
    open(conf, "w").write("mode = banana\n")
    assert tablet.read_mode(conf) == "auto"


# ---------------------------------------------------------------- school mode --

def test_school_mode_files_block_every_camera_path(tmp_path):
    paths = school.Paths(str(tmp_path))
    commands = []
    school.turn_on(paths, run=commands.append)
    modprobe = open(paths.modprobe).read()
    for module in ("uvcvideo", "intel_ipu6_isys", "intel_ipu6", "ipu3_cio2"):
        assert f"install {module} /bin/false" in modprobe and f"blacklist {module}" in modprobe
    rules = open(paths.udev).read()
    assert 'SUBSYSTEM=="video4linux", MODE="0000"' in rules and 'SUBSYSTEM=="media", MODE="0000"' in rules
    assert 'ATTR{bInterfaceClass}=="0e"' in rules and 'ATTR{authorized}="0"' in rules      # USB video class
    assert school.is_on(paths)
    assert ["udevadm", "control", "--reload"] in commands
    assert ["modprobe", "-r", "uvcvideo"] in commands            # already-loaded drivers are unloaded now


def test_school_mode_off_removes_everything_it_added(tmp_path):
    paths = school.Paths(str(tmp_path))
    school.turn_on(paths, run=lambda c: None)
    school.turn_off(paths, run=lambda c: None)
    assert not any(os.path.exists(p) for p in (paths.modprobe, paths.udev, paths.flag))
    school.turn_off(paths, run=lambda c: None)                   # idempotent


@pytest.mark.skipif(not shutil.which("udevadm"), reason="needs udevadm")
def test_the_udev_rules_are_syntactically_valid(tmp_path):
    rules = tmp_path / "60-lintabos-school-mode.rules"
    rules.write_text(school.render_udev())
    proc = subprocess.run(["udevadm", "verify", str(rules)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_inspect_reports_cameras_that_are_still_reachable(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    locked, open_ = dev / "video0", dev / "video1"
    locked.write_text(""), open_.write_text("")
    os.chmod(locked, 0o000)
    os.chmod(open_, 0o660)
    proc = tmp_path / "modules"
    proc.write_text("uvcvideo 200000 0 - Live 0x0\nsnd 1 0 - Live 0x0\n")
    paths = school.Paths(str(tmp_path / "root"))
    school.turn_on(paths, run=lambda c: None)
    status = school.inspect(paths, dev_root=str(dev), proc_modules=str(proc))
    assert status.on and not status.effective
    assert status.open_nodes == [str(open_)] and status.loaded_modules == ["uvcvideo"]
    os.chmod(open_, 0o000)
    proc.write_text("snd 1 0 - Live 0x0\n")
    assert school.inspect(paths, dev_root=str(dev), proc_modules=str(proc)).effective


def test_turning_it_off_is_the_one_that_asks_for_a_password():
    policy = open(os.path.join(os.path.dirname(__file__), "..", "payload/usr/share/polkit-1/actions/org.lintabos.school-mode.policy")).read()
    on_block = policy.split('id="org.lintabos.school-mode-on"')[1].split("</action>")[0]
    off_block = policy.split('id="org.lintabos.school-mode-off"')[1].split("</action>")[0]
    assert "<allow_active>yes</allow_active>" in on_block
    assert "<allow_active>auth_admin</allow_active>" in off_block        # not auth_admin_keep: asked every time


# -------------------------------------------------------------- Windows files --

def test_fstab_block_is_read_only_owned_by_the_user_and_listed_in_the_sidebar():
    block = winfiles.fstab_block("ABCD1234", 1000, 1000)
    line = [l for l in block.splitlines() if l.startswith("UUID=")][0]
    assert line.startswith("UUID=ABCD1234 /media/windows ntfs3 ro,uid=1000,gid=1000,")
    for option in ("nofail", "noauto", "x-systemd.automount", "x-gvfs-show", "x-gvfs-name=Windows"):
        assert option in line
    assert "rw," in winfiles.fstab_block("A", 1, 1, read_write=True)


def test_fstab_block_is_added_replaced_and_removed_without_touching_other_lines():
    original = "UUID=root / ext4 defaults 0 1\nUUID=esp /boot/efi vfat umask=0077 0 2\n"
    once = winfiles.apply_block(original, winfiles.fstab_block("AAAA", 1000, 1000))
    twice = winfiles.apply_block(once, winfiles.fstab_block("BBBB", 1000, 1000))
    assert twice.count(winfiles.BEGIN) == 1 and "UUID=BBBB" in twice and "UUID=AAAA" not in twice
    assert winfiles.apply_block(twice, None) == original
    assert winfiles.apply_block(original.rstrip("\n"), winfiles.fstab_block("C", 1, 1)).startswith(original.rstrip("\n") + "\n")


def test_installer_hook_writes_the_biggest_windows_partition(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=root / ext4 defaults 0 1\n")
    parts = [winfiles.WindowsPartition("/dev/sda3", "SMALL", 10), winfiles.WindowsPartition("/dev/sdb2", "BIG", 99)]
    chosen = winfiles.add_to_fstab(str(fstab), 1000, 1000, root=str(tmp_path), partitions=parts)
    assert chosen.uuid == "BIG" and "UUID=BIG" in fstab.read_text()
    assert (tmp_path / "media/windows").is_dir()
    assert winfiles.add_to_fstab(str(fstab), 1000, 1000, root=str(tmp_path), partitions=[]) is None


# ---------------------------------------------------------------- boot menu ----

def make_efivars(tmp_path, value):
    d = tmp_path / "efivars"
    d.mkdir(exist_ok=True)
    (d / bootmenu.EFIVAR_SECURE_BOOT).write_bytes(b"\x06\x00\x00\x00" + bytes([value]))
    return str(d)


def test_secure_boot_state_is_read_from_the_firmware_variable(tmp_path):
    assert bootmenu.secure_boot_enabled(make_efivars(tmp_path, 1)) is True
    assert bootmenu.secure_boot_enabled(make_efivars(tmp_path, 0)) is False
    assert bootmenu.secure_boot_enabled(str(tmp_path / "nothing-here")) is None


def test_menu_config_is_touch_only_and_lists_just_the_two_choices():
    conf = bootmenu.render_conf("/EFI/LintabOS/shimx64.efi", windows=True)
    assert "enable_touch" in conf and "enable_mouse" not in conf       # mutually exclusive in rEFInd
    assert "scanfor manual" in conf and "big_icon_size 256" in conf
    assert 'menuentry "LintabOS"' in conf and "loader /EFI/LintabOS/shimx64.efi" in conf
    assert 'menuentry "Windows"' in conf and "bootmgfw.efi" in conf
    assert 'menuentry "Windows"' not in bootmenu.render_conf("/EFI/LintabOS/shimx64.efi", windows=False)


def test_boot_order_puts_the_menu_first_without_duplicates():
    assert bootmenu.new_boot_order(["0001", "0000"], "0003") == ["0003", "0001", "0000"]
    assert bootmenu.new_boot_order(["0003", "0001"], "0003") == ["0003", "0001"]
    order, entries = bootmenu.parse_efibootmgr("BootCurrent: 0001\nBootOrder: 0001,0000\nBoot0000* Windows Boot Manager\tHD(1)\nBoot0001* LintabOS\tHD(1)\n")
    assert order == ["0001", "0000"] and entries == {"0000": "Windows Boot Manager", "0001": "LintabOS"}


class FakeFirmware:
    """Stands in for efibootmgr/findmnt/lsblk so enable()/disable() can run for real on a temp ESP."""

    def __init__(self):
        self.order = ["0001", "0000"]
        self.entries = {"0000": "Windows Boot Manager", "0001": "LintabOS"}
        self.calls = []

    def text(self):
        lines = [f"BootOrder: {','.join(self.order)}"] + [f"Boot{i}* {l}" for i, l in sorted(self.entries.items())]
        return "\n".join(lines) + "\n"

    def __call__(self, argv):
        self.calls.append(argv)
        out = ""
        if argv[0] == "efibootmgr":
            if len(argv) == 1:
                out = self.text()
            elif argv[1:3] == ["-c", "-d"]:
                self.entries["0003"] = bootmenu.ENTRY_LABEL
                self.order = ["0003"] + self.order
                out = self.text()
            elif argv[1] == "-B":
                self.entries.pop(argv[3], None)
                self.order = [b for b in self.order if b != argv[3]]
            elif argv[1] == "-o":
                self.order = argv[2].split(",")
            else:
                out = self.text()
        elif argv[0] == "findmnt":
            out = "/dev/sda1\n"
        elif argv[0] == "lsblk":
            out = "sda\n" if "PKNAME" in argv else "1\n"
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def esp(tmp_path):
    root = tmp_path / "esp"
    (root / "EFI/LintabOS").mkdir(parents=True)
    (root / "EFI/LintabOS/shimx64.efi").write_bytes(b"MZ")
    (root / "EFI/Microsoft/Boot").mkdir(parents=True)
    (root / "EFI/Microsoft/Boot/bootmgfw.efi").write_bytes(b"MZ")
    src = tmp_path / "refind-src"
    (src / "icons").mkdir(parents=True)
    (src / "refind_x64.efi").write_bytes(b"MZrefind")
    (src / "icons/os_win.png").write_bytes(b"png")
    return {"esp": str(root), "src": str(src), "tmp": tmp_path}


def test_enable_refuses_when_secure_boot_is_on_and_changes_nothing(esp):
    fw = FakeFirmware()
    with pytest.raises(bootmenu.BootMenuError, match="Secure Boot"):
        bootmenu.enable(esp["esp"], make_efivars(esp["tmp"], 1), run=fw, refind_source=esp["src"],
                        state_path=str(esp["tmp"] / "state.json"))
    assert not os.path.exists(os.path.join(esp["esp"], "EFI/refind")) and not fw.calls


def test_enable_installs_alongside_grub_and_disable_restores_everything(esp):
    fw = FakeFirmware()
    state = str(esp["tmp"] / "state.json")
    bootmenu.enable(esp["esp"], make_efivars(esp["tmp"], 0), run=fw, refind_source=esp["src"], state_path=state)
    target = os.path.join(esp["esp"], "EFI/refind")
    assert os.path.exists(os.path.join(target, "refind_x64.efi")) and os.path.exists(os.path.join(target, "icons/os_win.png"))
    conf = open(os.path.join(target, "refind.conf")).read()
    assert "loader /EFI/LintabOS/shimx64.efi" in conf and "bootmgfw.efi" in conf
    assert fw.order == ["0003", "0001", "0000"]                                    # menu first, GRUB and Windows still there
    assert os.path.exists(os.path.join(esp["esp"], "EFI/LintabOS/shimx64.efi"))   # GRUB untouched
    assert os.path.exists(os.path.join(esp["esp"], "EFI/Microsoft/Boot/bootmgfw.efi"))  # Windows untouched

    bootmenu.enable(esp["esp"], make_efivars(esp["tmp"], 0), run=fw, refind_source=esp["src"], state_path=state)
    assert list(fw.entries.values()).count(bootmenu.ENTRY_LABEL) == 1              # enabling twice doesn't pile up entries

    bootmenu.disable(run=fw, state_path=state)
    assert fw.order == ["0001", "0000"] and bootmenu.ENTRY_LABEL not in fw.entries.values()
    assert not os.path.exists(target) and not os.path.exists(state)


def test_disable_without_enable_explains_itself(tmp_path):
    with pytest.raises(bootmenu.BootMenuError, match="isn't enabled"):
        bootmenu.disable(run=FakeFirmware(), state_path=str(tmp_path / "none.json"))


def test_refind_is_installed_on_demand_with_its_own_boot_takeover_switched_off_first(tmp_path):
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    empty = str(tmp_path / "no-refind-here")
    assert bootmenu.ensure_refind(run, empty) is True
    assert "install_to_esp boolean false" in calls[0][-1] and "debconf-set-selections" in calls[0][-1]
    assert "apt-get" in calls[1] and "refind" in calls[1] and "--no-install-recommends" in calls[1]

    (tmp_path / "have").mkdir()
    (tmp_path / "have/refind_x64.efi").write_bytes(b"MZ")
    calls.clear()
    assert bootmenu.ensure_refind(run, str(tmp_path / "have")) is False and not calls


def test_refind_is_not_installed_if_the_safety_answer_cannot_be_stored(tmp_path):
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "no debconf")

    with pytest.raises(bootmenu.BootMenuError, match="not installing"):
        bootmenu.ensure_refind(run, str(tmp_path / "none"))
    assert len(calls) == 1                                          # apt-get was never reached


# ----------------------------------------------------- keyboard attach/detach message + snap sound --

def test_attach_and_detach_messages_and_the_first_look_is_silent():
    assert tablet.event_text(True, "laptop")[0] == "Keyboard Attached"
    assert tablet.event_text(False, "tablet")[0] == "Keyboard Detached"
    assert "on-screen keyboard off" in tablet.event_text(True, "laptop")[1]
    assert "on-screen keyboard on" in tablet.event_text(False, "tablet")[1]
    first = tablet.plan_refresh("auto", [TOUCHSCREEN, FOLIO], None, None)
    assert first == ("laptop", True, None, True)                  # state applied, nothing announced at login
    detached = tablet.plan_refresh("auto", [TOUCHSCREEN], True, "laptop")
    assert detached == ("tablet", False, False, True)
    attached = tablet.plan_refresh("auto", [TOUCHSCREEN, FOLIO], False, "tablet")
    assert attached == ("laptop", True, True, True)
    assert tablet.plan_refresh("auto", [TOUCHSCREEN, FOLIO], True, "laptop") == ("laptop", True, None, False)


def test_a_forced_mode_still_announces_the_physical_keyboard_but_does_not_flip_the_osk():
    state, attached, event, apply = tablet.plan_refresh("tablet", [TOUCHSCREEN, FOLIO], False, "tablet")
    assert (state, attached, event, apply) == ("tablet", True, True, False)


def test_sound_command_picks_the_right_file_and_player(tmp_path):
    (tmp_path / "keyboard-attached.wav").write_bytes(b"RIFF")
    (tmp_path / "keyboard-detached.wav").write_bytes(b"RIFF")
    have = lambda name: "/usr/bin/" + name if name == "paplay" else None
    assert tablet.sound_command(True, str(tmp_path), have) == ["paplay", str(tmp_path / "keyboard-attached.wav")]
    assert tablet.sound_command(False, str(tmp_path), have)[1].endswith("keyboard-detached.wav")
    assert tablet.sound_command(True, str(tmp_path), lambda n: None) is None          # no player: silent, no crash
    assert tablet.sound_command(True, str(tmp_path / "missing"), have) is None        # no file: silent, no crash


def test_the_generated_snaps_are_valid_short_audible_and_different(tmp_path):
    import importlib.util
    import wave
    spec = importlib.util.spec_from_file_location("gen", os.path.join(os.path.dirname(__file__), "..", "scripts", "gen_keyboard_sounds.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.main(["gen", str(tmp_path)]) == 0
    data = {}
    for kind in ("attached", "detached"):
        with wave.open(str(tmp_path / f"keyboard-{kind}.wav")) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 48000)
            assert 0.08 < w.getnframes() / w.getframerate() < 0.3                    # a snap, not a song
            data[kind] = w.readframes(w.getnframes())
    assert data["attached"] != data["detached"]
    import struct
    peak = max(abs(v) for v in struct.unpack("<%dh" % (len(data["attached"]) // 2), data["attached"]))
    assert 20000 < peak < 32767                                                      # loud enough, not clipped


# ------------------------------------------------- "Folio not working" (assist) mode --

def test_assist_mode_keeps_the_on_screen_keyboard_on_with_the_folio_attached():
    assert tablet.decide("assist", [TOUCHSCREEN, FOLIO]) == "tablet"            # folio attached, OSK still on
    assert tablet.decide("assist", []) == "tablet"
    assert "assist" in tablet.MODES
    # nothing in tablet state disables the folio: only the OSK setting changes
    for desktop in ("gnome", "kde", "xfce"):
        for command in tablet.osk_commands("tablet", desktop):
            assert not any(word in " ".join(command).lower() for word in ("disable", "inhibit", "unbind", "xinput"))


def test_assist_mode_is_remembered_and_still_announces_the_folio(tmp_path):
    conf = str(tmp_path / "tablet-mode.conf")
    tablet.write_mode("assist", conf)
    assert tablet.read_mode(conf) == "assist"
    title, body = tablet.event_text(True, "tablet", "assist")
    assert title == "Keyboard Attached" and "folio keys work" in body and "stays on" in body
    assert tablet.event_text(False, "tablet", "assist")[0] == "Keyboard Detached"
    state, attached, event, apply = tablet.plan_refresh("assist", [TOUCHSCREEN, FOLIO], False, "tablet")
    assert (state, attached, event, apply) == ("tablet", True, True, False)       # snap + message, OSK unchanged
