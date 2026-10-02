# SPDX-License-Identifier: MIT
"""lintab-wifi-fix: what it undoes, what it only reports, and that it never edits config."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import wifi  # noqa: E402

RFKILL_SOFT = "0: phy0: Wireless LAN\n\tSoft blocked: yes\n\tHard blocked: no\n1: hci0: Bluetooth\n\tSoft blocked: no\n\tHard blocked: no\n"
RFKILL_HARD = "0: phy0: Wireless LAN\n\tSoft blocked: no\n\tHard blocked: yes\n"
RFKILL_OK = "0: phy0: Wireless LAN\n\tSoft blocked: no\n\tHard blocked: no\n"
LSPCI_BOUND = "00:14.3 Network controller [0280]: Intel Corporation Alder Lake-N PCH CNVi WiFi [8086:54f0]\n\tKernel driver in use: iwlwifi\n\tKernel modules: iwlwifi\n00:02.0 VGA compatible controller: Intel\n\tKernel driver in use: i915\n"
LSPCI_UNBOUND = "00:14.3 Network controller [0280]: Intel Corporation WiFi [8086:54f0]\n\tKernel modules: iwlwifi\n"
NMCLI_WIFI = "wlan0:wifi:disconnected\nlo:loopback:unmanaged\n"


class Fake:
    """Scripted commands: matches by prefix and records what was run."""

    NAMES = {"rfkill_list": "rfkill list", "systemctl_is_active": "systemctl is-active", "systemctl_is_enabled": "systemctl is-enabled",
             "nmcli_radio_wifi": "nmcli radio wifi", "nmcli__t": "nmcli -t", "lspci": "lspci", "lsusb": "lsusb"}

    def __init__(self, **answers):
        self.answers, self.calls = {self.NAMES[k]: v for k, v in answers.items()}, []

    def __call__(self, argv):
        self.calls.append(argv)
        key = " ".join(argv)
        for prefix, reply in self.answers.items():
            if key.startswith(prefix):
                return reply if isinstance(reply, tuple) else (0, reply)
        return 0, ""

    def ran(self, text):
        return any(" ".join(c).startswith(text) for c in self.calls)


def kinds(lines):
    return [(l.kind, l.text) for l in lines]


def test_a_software_block_is_removed_and_a_hardware_block_is_only_reported(tmp_path):
    fake = Fake(rfkill_list=RFKILL_SOFT, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_BOUND,
                nmcli__t=NMCLI_WIFI)
    lines = wifi.fix(fake, root=str(tmp_path), cmdline="quiet")
    assert fake.ran("rfkill unblock wifi") and any(k == "FIXED" and "blocked by software" in t for k, t in kinds(lines))
    hard = Fake(rfkill_list=RFKILL_HARD, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_BOUND, nmcli__t=NMCLI_WIFI)
    lines = wifi.fix(hard, root=str(tmp_path), cmdline="quiet")
    assert not hard.ran("rfkill unblock") and any(k == "FAIL" and "HARD blocked" in t for k, t in kinds(lines))


def test_the_radio_and_services_are_switched_on_and_a_masked_service_is_unmasked(tmp_path):
    fake = Fake(rfkill_list=RFKILL_OK, systemctl_is_enabled="masked", systemctl_is_active="inactive", nmcli_radio_wifi="disabled",
                lspci=LSPCI_BOUND, nmcli__t=NMCLI_WIFI)
    lines = wifi.fix(fake, root=str(tmp_path), cmdline="quiet")
    assert fake.ran("systemctl unmask NetworkManager") and fake.ran("systemctl unmask wpa_supplicant")
    assert fake.ran("systemctl start NetworkManager") and fake.ran("nmcli networking on") and fake.ran("nmcli radio wifi on")
    assert sum(1 for k, _ in kinds(lines) if k == "FIXED") >= 3
    assert fake.ran("nmcli device wifi rescan")


def test_hardware_without_a_driver_gets_its_module_loaded():
    present, modules, in_use = wifi.wifi_modules_from_lspci(LSPCI_UNBOUND)
    assert (present, modules, in_use) == (True, ["iwlwifi"], "")
    assert wifi.wifi_modules_from_lspci(LSPCI_BOUND) == (True, ["iwlwifi"], "iwlwifi")
    assert wifi.wifi_modules_from_lspci("00:02.0 VGA compatible controller: Intel\n\tKernel driver in use: i915\n")[0] is False
    fake = Fake(rfkill_list=RFKILL_OK, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_UNBOUND, nmcli__t=NMCLI_WIFI)
    lines = wifi.fix(fake, root="/nonexistent", cmdline="")
    assert fake.ran("modprobe iwlwifi") and any(k == "FIXED" and "loaded driver module 'iwlwifi'" in t for k, t in kinds(lines))


def test_reload_driver_unloads_then_loads_only_when_asked():
    fake = Fake(rfkill_list=RFKILL_OK, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_BOUND, nmcli__t=NMCLI_WIFI)
    wifi.fix(fake, root="/nonexistent", cmdline="")
    assert not fake.ran("modprobe")                                       # bound driver, no flag: leave it alone
    fake = Fake(rfkill_list=RFKILL_OK, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_BOUND, nmcli__t=NMCLI_WIFI)
    wifi.fix(fake, reload_driver=True, root="/nonexistent", cmdline="")
    order = [" ".join(c) for c in fake.calls if c[0] == "modprobe"]
    assert order == ["modprobe -r iwlwifi", "modprobe iwlwifi"]


def test_no_wifi_hardware_is_reported_honestly():
    fake = Fake(rfkill_list="", systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci="00:02.0 VGA compatible controller: Intel\n",
                lsusb="Bus 001 Device 002: ID 27c6:6512 Goodix\n", nmcli__t="lo:loopback:unmanaged\n")
    lines = wifi.fix(fake, root="/nonexistent", cmdline="")
    assert any(k == "FAIL" and "no Wi-Fi hardware is detected" in t for k, t in kinds(lines))
    assert any(k == "FAIL" and "still has no Wi-Fi device" in t for k, t in kinds(lines))
    assert not fake.ran("modprobe")


def test_blockers_in_config_are_reported_and_never_edited(tmp_path):
    (tmp_path / "etc/modprobe.d").mkdir(parents=True)
    bad = tmp_path / "etc/modprobe.d/disable.conf"
    bad.write_text("blacklist iwlwifi\ninstall rtw89_core /bin/false\nblacklist nouveau\n")
    (tmp_path / "etc/NetworkManager/conf.d").mkdir(parents=True)
    (tmp_path / "etc/NetworkManager/conf.d/x.conf").write_text("[keyfile]\nunmanaged-devices=interface-name:wl*\n")
    (tmp_path / "etc/network").mkdir()
    (tmp_path / "etc/network/interfaces").write_text("auto lo\nallow-hotplug wlan0\niface wlan0 inet dhcp\n")
    before = bad.read_text()
    problems = wifi.find_blockers([str(tmp_path / "etc/modprobe.d")], [str(tmp_path / "etc/NetworkManager/conf.d")],
                                  str(tmp_path / "etc/network/interfaces"), "quiet rfkill.default_state=0")
    text = "\n".join(problems)
    assert "iwlwifi" in text and "rtw89_core" in text and "nouveau" not in text           # only Wi-Fi modules
    assert "unmanaged-devices" in text and "interfaces" in text and "rfkill.default_state=0" in text
    assert bad.read_text() == before                                                      # reported, not changed
    assert wifi.find_blockers([str(tmp_path / "none")], [], str(tmp_path / "none"), "quiet") == []


def test_missing_firmware_named_in_the_kernel_log_is_reported(tmp_path):
    log = ("iwlwifi 0000:00:14.3: Direct firmware load for iwlwifi-so-a0-hr-b0-89.ucode failed with error -2\n"
           "iwlwifi 0000:00:14.3: no suitable firmware found!\n")
    problems = wifi.firmware_problems(log, str(tmp_path))
    assert problems and "iwlwifi-so-a0-hr-b0-89.ucode" in problems[0]
    (tmp_path / "iwlwifi-so-a0-hr-b0-89.ucode.zst").write_bytes(b"x")
    assert not any("iwlwifi-so-a0-hr-b0-89.ucode" in p for p in wifi.firmware_problems(log, str(tmp_path)))
    assert wifi.firmware_problems("all fine\n", str(tmp_path)) == []


def test_it_needs_root_and_can_save_its_result(tmp_path, capsys):
    assert wifi.main([], euid=1000) == 1 and "sudo lintab-wifi-fix" in capsys.readouterr().err
    fake = Fake(rfkill_list=RFKILL_OK, systemctl_is_active="active", nmcli_radio_wifi="enabled", lspci=LSPCI_BOUND, nmcli__t=NMCLI_WIFI)
    log = tmp_path / "out.txt"
    assert wifi.main(["--log", str(log)], euid=0, run=fake) == 0
    assert "NetworkManager now has a Wi-Fi device" in log.read_text() and "saved to" in capsys.readouterr().out
